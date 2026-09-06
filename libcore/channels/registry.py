"""通道注册表：channel_id -> Channel。

能力节点（im/ui/cli）出站时经本注册表找到对应 Channel，把回复送回原会话。
只依赖 Channel 契约，不依赖具体实现；加/改一个通道不影响其他通道。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import Channel


class ChannelRegistry:
    """通道注册表：channel_id -> Channel。"""

    def __init__(self) -> None:
        self._channels: Dict[str, Channel] = {}

    def register(self, channel: Channel) -> None:
        """注册一个通道。channel_id 唯一，重复注册覆盖。"""
        self._channels[channel.channel_id] = channel

    def get(self, channel_id: str) -> Optional[Channel]:
        """按 channel_id 取通道；不存在返回 None。"""
        return self._channels.get(channel_id)

    def channels(self) -> List[Channel]:
        """列出所有已注册通道。"""
        return list(self._channels.values())
