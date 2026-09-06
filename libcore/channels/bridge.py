"""薄桥接：把入站广播 channel.inbound 接到现有常驻内核（ResidentKernel）。

**不新增调度器**。ResidentKernel 本身就是"收到请求 → 驱动 AgentLoop"的调度核心
（libcore/kernel/agent/resident.py），本桥接只是几行代码，把入站广播接到它：
收到消息就 submit，由内核驱动 AgentLoop 处理。

IM / UI / CLI 的入站全部汇聚到同一个 ResidentKernel，零新增、零内核改动。
"""
from __future__ import annotations

from typing import Any

from libcore.kernel.bus import EventBus, Notice
from libcore.kernel.agent import ResidentKernel


def wire_inbound_to_kernel(bus: EventBus, kernel: ResidentKernel) -> None:
    """把入站广播接到现有常驻内核：收到消息就 submit，由内核驱动 AgentLoop。

    - ``kernel.submit(goal, reason=...)``：goal 携带 text 与 channel_id，
      内核传入 AgentLoop 的 session_id 保持会话连续性。
    - ``reason``：复用内核的决策者实例（子 agent 独立大脑由内核管理）。
    """

    async def _on_inbound(notice: Notice) -> None:
        payload: dict = notice.payload
        kernel.submit(
            {"goal": payload.get("text", ""), "channel_id": payload.get("channel_id")},
            reason=kernel.reason,
        )

    bus.subscribe("channel.inbound", _on_inbound)
