"""Channel 抽象基类：外部对话入口的统一契约。

每个外部入口（IM 平台 / UI 会话 / CLI 终端）实现一个 Channel：
- ``ingest``：入站，把外部消息规整成统一 InboundMessage，广播进总线。
- ``reply``：出站，把 agent 回复送回原会话。

Channel 只依赖 EventBus（广播入站）与 ChannelRegistry（出站查表），
不依赖 SessionStore、不依赖其他 Channel、不依赖内核。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .message import InboundMessage, OutboundMessage


class Channel(ABC):
    """外部对话入口的抽象：入站 ingest + 出站 reply。"""

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """唯一通道 id（即 session_id，打通会话维度）。"""

    @property
    @abstractmethod
    def kind(self) -> str:
        """通道类型：im / ui / cli。"""

    @abstractmethod
    def ingest(self, message: InboundMessage) -> None:
        """入站：把外部消息规整成统一 InboundMessage，广播进总线。"""

    @abstractmethod
    def reply(self, message: OutboundMessage) -> None:
        """出站：把 agent 回复送回原会话。"""
