"""统一消息结构：入站（外部入口 → agent）与出站（agent → 外部入口）。

消息只携带 ``channel_id``（即 session_id，打通会话维度），不携带任何
平台/通道实现细节。Channel 负责把平台原始消息规整成统一结构，agent 只认
``channel_id``，不关心背后是哪个平台。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class InboundMessage:
    """入站消息：外部入口 → agent。

    - ``channel_id``：哪个通道（即 session_id，打通会话维度）。
    - ``kind``：通道类型（im / ui / cli）。
    - ``platform``：平台（im 用 feishu/weixin/...；ui/cli 用 "ui"/"cli"）。
    - ``user_id``：发送者。
    - ``text``：消息正文。
    - ``raw``：平台原始消息（可选，供调试/扩展）。
    """

    channel_id: str
    kind: str
    platform: str
    user_id: str
    text: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """出站消息：agent → 外部入口。"""

    channel_id: str
    kind: str
    platform: str
    text: str
