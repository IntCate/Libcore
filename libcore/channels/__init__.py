"""通道（Channel）统一通讯机制：IM / UI / CLI 本质是同一套"外部入口发起对话"链路。

- ``Channel``：外部对话入口的抽象（入站 ingest / 出站 reply）。
- ``ChannelRegistry``：channel_id -> Channel 的查表，能力节点据此把回复送回原会话。
- ``InboundMessage`` / ``OutboundMessage``：统一消息结构。

设计原则（见 docs/libcore-channel-design.md）：
- 内核零改动：只依赖 EventBus 的 publish / dispatch 两个原语。
- 插件层只动自己：加/改一个通道，不互相改。
- 装配层配置驱动：加通道只改 channels.yaml，不动装配代码。
"""
from .message import InboundMessage, OutboundMessage
from .base import Channel
from .registry import ChannelRegistry
from .im_channel import ImChannel
from .ui_channel import UiChannel
from .cli_channel import CliChannel

__all__ = [
    "InboundMessage",
    "OutboundMessage",
    "Channel",
    "ChannelRegistry",
    "ImChannel",
    "UiChannel",
    "CliChannel",
]
