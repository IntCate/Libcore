"""Kernel 装配：组合 EventBus + AgentLoop + 能力 + 横切面。

装配期一次性完成：能力进点名表、护栏挂横切管，之后内核不再改动。
"""
from __future__ import annotations

from typing import Iterable, List

from .core.bus import EventBus, Aspect
from .core.event import CorrelationId
from .agent.loop import AgentLoop
from .agent.spi import ReasonProvider


class Kernel:
    """libcore 最小内核的可运行组合根。"""

    def __init__(self, bus: EventBus, loop: AgentLoop) -> None:
        self.bus = bus
        self.loop = loop

    async def run(self, goal) -> object:
        return await self.loop.run(goal)

    @classmethod
    def bootstrap(
        cls,
        *,
        targets: dict,
        aspects: Iterable[Aspect] = (),
        reason: ReasonProvider | None = None,
    ) -> "Kernel":
        """便捷装配：把 ``{target: handler}`` 能力与护栏组装成可用内核。"""
        bus = EventBus()
        for target, handler in targets.items():
            bus.on(target, handler)
        for aspect in aspects:
            bus.add_aspect(aspect)
        loop = AgentLoop(bus, reason)
        return cls(bus, loop)