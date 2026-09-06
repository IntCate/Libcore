"""im 平台适配器：Telegram。

接入逻辑借鉴 hermes-agent 的 ``plugins/platforms/telegram/adapter.py``
（只借鉴 ``python-telegram-bot`` 发消息与 MarkdownV2 转义，不引入其网关级抽象）。

依赖：``python-telegram-bot``（``from telegram import Bot``）。
配置：``TELEGRAM_BOT_TOKEN``（必填）。

契约：``adapter(op, payload) -> dict``。当前支持 ``send``（文本）与 ``status``。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict

try:
    from telegram import Bot
except ImportError:  # 未安装依赖时惰性降级
    Bot = None  # type: ignore[assignment]

_MDV2_ESCAPE_RE = re.compile(r'([_*\[\]()~`>#\+\-=|{}.!\\])')


def _escape_mdv2(text: str) -> str:
    """转义 Telegram MarkdownV2 特殊字符（借鉴 hermes）。"""
    return _MDV2_ESCAPE_RE.sub(r'\\\1', text)


class TelegramAdapter:
    """Telegram 适配器：用 ``python-telegram-bot`` 发文本消息。"""

    def __init__(self, bot_token: str) -> None:
        if Bot is None:
            raise RuntimeError("python-telegram-bot 未安装，无法使用 Telegram 适配器")
        self._bot = Bot(token=bot_token)

    def __call__(self, op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if op == "send":
            return self._send(payload)
        if op == "status":
            return {"connected": True, "platform": "telegram"}
        raise NotImplementedError(f"telegram 不支持 op: {op}")

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        chat_id = payload.get("chat_id")
        text = payload.get("text")
        if not chat_id or not text:
            raise ValueError("telegram send 需要 chat_id 与 text")
        escaped = _escape_mdv2(text)
        result = asyncio.run(self._bot.send_message(
            chat_id=chat_id, text=escaped, parse_mode="MarkdownV2",
        ))
        return {"message_id": str(result.message_id)}
