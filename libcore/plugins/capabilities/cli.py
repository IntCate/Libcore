"""内置能力插件：cli —— 终端对话入口（agentOS 系统能力）。

语义：agent 接管系统核心后，**CLI 入口**是 agent 可调度的能力节点。
agent 决定向终端输出什么文本。

产出约定：``data["status"]`` 为输出结果。
无输出后端 → 降级为 not_implemented（诚实披露，绝不编造输出成功）。

装配（零耦合，可拆卸）：
- ``register(bus, sink=)`` 可注入输出后端（签名 ``sink(text) -> None``，默认 print）；
- ``register(bus, channels=)`` 可注入通道注册表（ChannelRegistry）：当 payload 带
  ``channel_id`` 时，send 优先经对应 Channel.reply 送回原会话（统一出站链路），
  否则回退到 sink（兼容现有主动输出）；
- 本节点不 dispatch 任何其他节点。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from libcore.kernel.bus import Dispatch, CapabilityResult

DESCRIPTION = (
    "系统能力节点：agent 调度我向终端输出文本。send 决定输出什么文本。"
    "无输出后端降级为 not_implemented。"
)

# sink 类型：接收文本，输出到终端
Sink = Callable[[str], Any]


def _default_sink(text: str) -> None:
    print(text)


def register(bus, sink: Optional[Sink] = None, channels=None) -> None:
    """注册 cli 能力节点。

    - ``sink``：可注入输出后端（签名 ``sink(text) -> None``）；缺省默认 print。
    - ``channels``：可注入通道注册表（ChannelRegistry）。当 payload 带 ``channel_id``
      且注册表中有对应通道时，send 优先经 ``Channel.reply`` 送回原会话（统一出站链路）；
      否则回退到 sink（兼容现有主动输出）。
    """
    out = sink or _default_sink

    def handle(d: Dispatch) -> CapabilityResult:
        op = d.op or "send"
        payload = d.payload or {}

        # 统一出站：payload 带 channel_id 且注册表有对应通道 → 经 Channel.reply 送回原会话
        channel_id = payload.get("channel_id")
        if op == "send" and channel_id and channels is not None:
            channel = channels.get(channel_id)
            if channel is not None:
                from libcore.channels.message import OutboundMessage
                channel.reply(OutboundMessage(
                    channel_id=channel_id, kind=channel.kind, platform="cli",
                    text=payload.get("text", ""),
                ))
                return CapabilityResult(ok=True, data={"status": "sent", "channel_id": channel_id})

        text = payload.get("text", "")
        try:
            out(text)
        except Exception as e:
            return CapabilityResult(ok=False, data={}, error=f"cli 输出失败: {e}")
        return CapabilityResult(ok=True, data={"status": "sent"})

    bus.on("cli", handle, meta={
        "description": DESCRIPTION,
        "ops": ["send"],
    })
