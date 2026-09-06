"""UI 通道：前端聊天框 → agent → 渲染回前端。

入站：前端"用户发送消息"事件 → UiChannel.ingest → 广播 channel.inbound。
出站：agent dispatch("ui", op="render", {channel_id, text}) → UiChannel.reply
      → 经注入的 sink（RenderSink）下行到前端。

UiChannel 只依赖 EventBus（广播入站）与注入的 sink（出站下行），
不依赖 SessionStore、不依赖其他 Channel、不依赖内核。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict

from libcore.kernel.bus import EventBus, Notice

from .base import Channel
from .message import InboundMessage, OutboundMessage

# 出站下行回调：接收渲染消息 dict，push 到前端（如 RenderSink）
Sink = Callable[[Dict[str, Any]], Any]


class UiChannel(Channel):
    """UI 对话入口：一个前端会话对应一个 UiChannel。"""

    def __init__(self, bus: EventBus, session_id: str, sink: Sink) -> None:
        self._bus = bus
        self._sink = sink
        self._session_id = session_id
        self._channel_id = f"ui:{session_id}"

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def kind(self) -> str:
        return "ui"

    def ingest(self, message: InboundMessage) -> None:
        """入站：把前端消息广播进总线，供薄桥接转交 ResidentKernel。"""
        asyncio.create_task(self._bus.publish(Notice(
            topic="channel.inbound",
            payload={
                "channel_id": self._channel_id,
                "kind": self.kind,
                "platform": "ui",
                "user_id": message.user_id,
                "text": message.text,
            },
        )))

    def reply(self, message: OutboundMessage) -> None:
        """出站：把 agent 回复经 sink 下行到前端。"""
        self._sink({
            "type": "data",
            "target": f"/root/{self._session_id}",
            "data": {"channel_id": self._channel_id, "text": message.text},
        })
