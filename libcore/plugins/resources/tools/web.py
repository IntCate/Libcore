"""工具定义：网络 抓取 / 搜索（收进 tools 门面，不再独立注册总线节点）。

由旧架构 WebFetchTool / WebSearchTool 重写而来：
- web fetch：抓取 URL 文本（httpx，惰性加载）；
- web search：后端未接线，如实返回 not_implemented（绝不编造）。
handler 统一签名 ``(d: Dispatch) -> CapabilityResult``，按 ``d.op`` 分发 fetch/search。
"""
from __future__ import annotations

from libcore.kernel.bus import Dispatch, CapabilityResult


def _fetch(d: Dispatch) -> CapabilityResult:
    url = d.payload.get("url")
    if not url:
        return CapabilityResult(ok=False, error="缺少必填参数 url")
    try:
        max_chars = int(d.payload.get("max_chars") or 10000)
    except (TypeError, ValueError):
        max_chars = 10000
    try:
        import httpx  # 惰性加载：httpx 未必随内核安装

        resp = httpx.get(url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        status_code, text = resp.status_code, resp.text
    except ImportError:
        return CapabilityResult(
            ok=False, error="httpx 未安装，无法执行网络请求",
            data={"status": "not_implemented", "url": url},
        )
    except Exception as e:  # noqa: BLE001
        return CapabilityResult(ok=False, error=str(e), data={"url": url})
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... [truncated {len(text) - max_chars} chars]"
    return CapabilityResult(ok=True, data={"url": url, "status_code": status_code, "content": text})


def _search(d: Dispatch) -> CapabilityResult:
    query = d.payload.get("query")
    if not query:
        return CapabilityResult(ok=False, error="缺少必填参数 query")
    try:
        max_results = int(d.payload.get("max_results") or 5)
    except (TypeError, ValueError):
        max_results = 5
    return CapabilityResult(
        ok=False,
        error="搜索后端未配置，暂时无法返回结果",
        data={"status": "not_implemented", "query": query, "max_results": max_results},
    )


def _handle(d: Dispatch) -> CapabilityResult:
    op = d.op or d.payload.get("op") or "fetch"
    fn = {"fetch": _fetch, "search": _search}.get(op)
    if fn is None:
        return CapabilityResult(ok=False, error=f"未知操作 {op}")
    return fn(d)


TOOLS = {
    "web": {
        "name": "web",
        "description": "抓取指定 URL 的文本内容，或按关键词搜索（搜索未接线）",
        "ops": ["fetch", "search"],
        "schema": {
            "fetch": {"url": "str, 要抓取的 URL", "max_chars": "int, 截断长度（默认 10000）"},
            "search": {"query": "str, 搜索关键词", "max_results": "int, 返回条数（默认 5）"},
        },
        "handler": _handle,
    }
}