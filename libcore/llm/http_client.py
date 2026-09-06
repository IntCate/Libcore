"""Keepalive HTTP 客户端（翻译自 Hermes build_keepalive_http_client）。

设计要点：
    1. 跨请求复用 httpx 连接池（比每次新建快 50-100ms/请求）。
    2. SDK 内部 max_retries=0，由外层 retry.py 统一控制重试。
    3. 双层超时：Header timeout（连接/TLS/首字节）vs Body timeout（SSE chunk 间隔）。
    4. 支持 TLS/代理/超时配置注入。
    5. SSE 超时保护 wrapper（OpenCode wrapSSE 思路）：流式 hang 不拖死 agent。

不变量守护：
    仅 import httpx（通用包白名单），无 provider SDK，零 langchain_ 引用。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional

import httpx


# ============================================================
# 1. 配置数据结构
# ============================================================

@dataclass
class HttpClientConfig:
    """HTTP 客户端配置。

    超时分两层（OpenCode timeoutController 思路）：
        header_timeout_ms: 连接 + TLS + 等待响应头（网络问题）
        body_timeout_ms:   响应体读取/SSE chunk 间隔（server 卡住）

    SDK 内部 max_retries=0，重试由 retry.py 统一处理。
    """
    header_timeout_ms: int = 60_000
    body_timeout_ms: int = 180_000
    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry_s: float = 30.0
    proxy_url: Optional[str] = None
    verify_ssl: bool = True
    extra_headers: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "HttpClientConfig":
        """从环境变量读取代理/超时配置（适配企业内网环境）。"""
        cfg = cls()
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
            or os.environ.get("ALL_PROXY")
            or os.environ.get("all_proxy")
        )
        if proxy:
            cfg.proxy_url = proxy
        if os.environ.get("LIBCORE_LLM_HEADER_TIMEOUT_MS"):
            cfg.header_timeout_ms = int(os.environ["LIBCORE_LLM_HEADER_TIMEOUT_MS"])
        if os.environ.get("LIBCORE_LLM_BODY_TIMEOUT_MS"):
            cfg.body_timeout_ms = int(os.environ["LIBCORE_LLM_BODY_TIMEOUT_MS"])
        return cfg

    def to_httpx_timeout(self) -> httpx.Timeout:
        """转换为 httpx.Timeout（区分 connect/read/write/pool）。"""
        return httpx.Timeout(
            connect=self.header_timeout_ms / 1000.0,
            read=self.body_timeout_ms / 1000.0,
            write=self.header_timeout_ms / 1000.0,
            pool=self.header_timeout_ms / 1000.0,
        )

    def to_httpx_limits(self) -> httpx.Limits:
        """转换为 httpx.Limits（连接池大小）。"""
        return httpx.Limits(
            max_connections=self.max_connections,
            max_keepalive_connections=self.max_keepalive_connections,
            keepalive_expiry=self.keepalive_expiry_s,
        )


# ============================================================
# 2. 客户端构造
# ============================================================

def build_keepalive_http_client(
    *,
    base_url: Optional[str] = None,
    config: Optional[HttpClientConfig] = None,
    headers: Optional[Dict[str, str]] = None,
) -> httpx.AsyncClient:
    """构造 keepalive httpx.AsyncClient。

    用法：
        client = build_keepalive_http_client(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        # 跨多个 chat 请求复用同一 client
        resp = await client.post("/chat/completions", json=payload)
    """
    cfg = config or HttpClientConfig.from_env()

    final_headers: Dict[str, str] = {"Accept": "application/json"}
    final_headers.update(cfg.extra_headers)
    if headers:
        final_headers.update(headers)

    proxies: Optional[str] = cfg.proxy_url

    client_kwargs: Dict[str, Any] = {
        "base_url": base_url or "",
        "timeout": cfg.to_httpx_timeout(),
        "limits": cfg.to_httpx_limits(),
        "headers": final_headers,
        "verify": cfg.verify_ssl,
        # 关键：transport 层 max_retries=0，由 retry.py 统一控制重试
        "transport": httpx.AsyncHTTPTransport(retries=0),
    }
    if proxies:
        client_kwargs["proxy"] = proxies

    return httpx.AsyncClient(**client_kwargs)


# ============================================================
# 3. SSE 超时保护 wrapper（OpenCode wrapSSE 思路）
# ============================================================

class SSETimeoutError(Exception):
    """SSE 流超时（chunk 间隔超时，区别于 HTTP header 超时）。"""


async def wrap_sse_stream(
    response: httpx.Response,
    chunk_timeout_s: float = 60.0,
) -> AsyncIterator[str]:
    """对 SSE 响应体加超时保护。

    用法：
        async with client.stream("POST", url, json=payload) as resp:
            async for line in wrap_sse_stream(resp, chunk_timeout_s=60):
                parse_sse_line(line)

    注意：本函数不管理 response 生命周期（由调用方的 `async with client.stream(...)` 负责），
    仅对 aiter_lines() 加 chunk 间隔超时。

    yield 行（已去 \\n）；空行（SSE event 分隔符）也会 yield。
    """
    import asyncio

    aiter = response.aiter_lines()
    while True:
        # 用 asyncio.wait 而非 asyncio.wait_for：wait_for 对 StopAsyncIteration
        # 的处理在某些 Python 版本不可靠（会被吞成 CancelledError）。
        task = asyncio.ensure_future(aiter.__anext__())
        try:
            done, pending = await asyncio.wait({task}, timeout=chunk_timeout_s)
        except Exception:
            task.cancel()
            raise
        if not done:
            task.cancel()
            raise SSETimeoutError(
                f"SSE chunk 间隔超时（>{chunk_timeout_s}s 无数据）"
            )
        try:
            line = task.result()
        except StopAsyncIteration:
            return
        yield line


# ============================================================
# 4. 便捷请求函数（adapter 复用）
# ============================================================

async def post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: Dict[str, Any],
    *,
    headers: Optional[Dict[str, str]] = None,
) -> httpx.Response:
    """POST JSON 请求（非流式）。"""
    return await client.post(url, json=payload, headers=headers or {})


async def post_stream(
    client: httpx.AsyncClient,
    url: str,
    payload: Dict[str, Any],
    *,
    headers: Optional[Dict[str, str]] = None,
) -> AsyncIterator[str]:
    """POST JSON 流式请求，yield SSE 行（带超时保护）。"""
    async with client.stream("POST", url, json=payload, headers=headers or {}) as resp:
        if resp.status_code >= 400:
            body = await resp.aread()
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}: {body.decode('utf-8', errors='replace')}",
                request=resp.request,
                response=resp,
            )
        async for line in wrap_sse_stream(resp):
            yield line
