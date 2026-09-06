"""im 平台适配器：Slack。

接入逻辑借鉴 hermes-agent 的 ``plugins/platforms/slack/adapter.py``
（只借鉴 ``slack_sdk`` 发消息的 SDK 调用方式，不引入其网关级抽象）。

依赖：``slack-sdk``（``from slack_sdk import WebClient``）。
配置：``SLACK_BOT_TOKEN``（``xoxb-...``，必填）。

契约：``adapter(op, payload) -> dict``。当前支持 ``send``（文本）与 ``status``。
"""
from __future__ import annotations

from typing import Any, Dict

try:
    from slack_sdk import WebClient
except ImportError:  # 未安装依赖时惰性降级
    WebClient = None  # type: ignore[assignment]


class SlackAdapter:
    """Slack 适配器：用 ``WebClient`` 发文本消息。"""

    def __init__(self, bot_token: str) -> None:
        if WebClient is None:
            raise RuntimeError("slack-sdk 未安装，无法使用 Slack 适配器")
        self._client = WebClient(token=bot_token)

    def __call__(self, op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if op == "send":
            return self._send(payload)
        if op == "status":
            return {"connected": True, "platform": "slack"}
        raise NotImplementedError(f"slack 不支持 op: {op}")

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        chat_id = payload.get("chat_id")
        text = payload.get("text")
        if not chat_id or not text:
            raise ValueError("slack send 需要 chat_id 与 text")
        response = self._client.chat_postMessage(channel=chat_id, text=text)
        if not response["ok"]:
            raise RuntimeError(f"slack send failed: {response.get('error')}")
        return {"message_id": response["ts"]}
