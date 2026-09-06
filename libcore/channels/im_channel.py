"""IM 通道：IM 平台会话 → agent → 回复回原会话。

入站：平台监听器收到消息 → ImChannel.ingest → 广播 channel.inbound。
出站：agent dispatch("im", op="send", {channel_id, text}) → ImChannel.reply
      → 经注入的 sender（平台适配器）发回原会话。

ImChannel 只依赖 EventBus（广播入站）与注入的 sender（出站发送），
不依赖 SessionStore、不依赖其他 Channel、不依赖内核。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict

from libcore.kernel.bus import EventBus, Notice

from .base import Channel
from .message import InboundMessage, OutboundMessage

# 出站发送回调：接收平台 + 会话 + 文本，发回原会话（如 FeishuAdapter）
Sender = Callable[[str, str, str], Dict[str, Any]]


class ImChannel(Channel):
    """IM 对话入口：一个平台会话（chat_id）对应一个 ImChannel。"""

    def __init__(self, bus: EventBus, platform: str, chat_id: str,
                 user_id: str, sender: Sender) -> None:
        self._bus = bus
        self._platform = platform
        self._chat_id = chat_id
        self._user_id = user_id
        self._sender = sender
        self._channel_id = f"im:{platform}:{chat_id}"

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def kind(self) -> str:
        return "im"

    def ingest(self, message: InboundMessage) -> None:
        """入站：把平台消息广播进总线，供薄桥接转交 ResidentKernel。"""
        asyncio.create_task(self._bus.publish(Notice(
            topic="channel.inbound",
            payload={
                "channel_id": self._channel_id,
                "kind": self.kind,
                "platform": self._platform,
                "user_id": message.user_id or self._user_id,
                "text": message.text,
            },
        )))

    def reply(self, message: OutboundMessage) -> None:
        """出站：把 agent 回复经 sender 发回原会话。"""
        self._sender(self._platform, self._chat_id, message.text)
