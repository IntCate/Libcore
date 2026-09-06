"""CLI 通道：终端输入 → agent → 打印回终端。

入站：终端输入 → CliChannel.ingest → 广播 channel.inbound。
出站：agent dispatch("cli", op="send", {channel_id, text}) → CliChannel.reply
      → 打印到终端（stdout）。

CliChannel 只依赖 EventBus（广播入站）与注入的 sink（出站打印），
不依赖 SessionStore、不依赖其他 Channel、不依赖内核。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict

from libcore.kernel.bus import EventBus, Notice

from .base import Channel
from .message import InboundMessage, OutboundMessage

# 出站打印回调：接收文本，打印到终端（默认 print）
Sink = Callable[[str], Any]


def _default_sink(text: str) -> None:
    print(text)


class CliChannel(Channel):
    """CLI 对话入口：单终端场景固定一个 channel_id（cli:stdin）。"""

    def __init__(self, bus: EventBus, sink: Sink = _default_sink) -> None:
        self._bus = bus
        self._sink = sink
        self._channel_id = "cli:stdin"

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def kind(self) -> str:
        return "cli"

    def ingest(self, message: InboundMessage) -> None:
        """入站：把终端输入广播进总线，供薄桥接转交 ResidentKernel。"""
        asyncio.create_task(self._bus.publish(Notice(
            topic="channel.inbound",
            payload={
                "channel_id": self._channel_id,
                "kind": self.kind,
                "platform": "cli",
                "user_id": message.user_id or "cli-user",
                "text": message.text,
            },
        )))

    def reply(self, message: OutboundMessage) -> None:
        """出站：把 agent 回复打印到终端。"""
        self._sink(message.text)
