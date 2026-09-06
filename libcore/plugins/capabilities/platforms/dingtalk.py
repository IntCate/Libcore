"""im 平台适配器：钉钉（DingTalk）。

接入逻辑借鉴 hermes-agent 的 ``plugins/platforms/dingtalk/adapter.py``
（只借鉴静态 webhook 的 HTTP 发送方式，不引入其 Stream Mode 长连接与 AI Card）。

依赖：``aiohttp``。
配置：``DINGTALK_WEBHOOK_URL``（必填，静态机器人 webhook URL）。

契约：``adapter(op, payload) -> dict``。当前支持 ``send``（文本）与 ``status``。
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict

try:
    import aiohttp
except ImportError:  # 未安装依赖时惰性降级
    aiohttp = None  # type: ignore[assignment]


class DingTalkAdapter:
    """钉钉适配器：用静态机器人 webhook 发文本消息。"""

    def __init__(self, webhook_url: str) -> None:
        if aiohttp is None:
            raise RuntimeError("aiohttp 未安装，无法使用钉钉适配器")
        self._webhook_url = webhook_url

    def __call__(self, op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if op == "send":
            return self._send(payload)
        if op == "status":
            return {"connected": bool(self._webhook_url), "platform": "dingtalk"}
        raise NotImplementedError(f"dingtalk 不支持 op: {op}")

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        text = payload.get("text")
        if not text:
            raise ValueError("dingtalk send 需要 text")
        body = {"msgtype": "text", "text": {"content": text}}
        result = asyncio.run(self._post(body))
        if result.get("errcode", 0) != 0:
            raise RuntimeError(f"dingtalk send failed: {result.get('errmsg', result)}")
        return {"message_id": uuid.uuid4().hex[:12]}

    async def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(self._webhook_url, json=body, timeout=15.0) as response:
                raw = await response.text()
                if not response.ok:
                    raise RuntimeError(f"dingtalk webhook HTTP {response.status}: {raw[:200]}")
                return await response.json()
