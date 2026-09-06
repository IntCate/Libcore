"""Kernel 装配：组合 EventBus + AgentLoop + 能力 + 横切面。

装配期一次性完成：能力进点名表、护栏挂横切管，之后内核不再改动。
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from .bus import EventBus, Aspect
from .bus import (
    CorrelationId,
    Dispatch,
    Notice,
    CapabilityResult,
    Scope,
)
from .agent import AgentLoop, ReasonProvider, ResidentKernel


class Kernel:
    """libcore 最小内核的可运行组合根。"""

    def __init__(self, bus: EventBus, loop: AgentLoop, resident: ResidentKernel | None = None) -> None:
        self.bus = bus
        self.loop = loop
        self.resident = resident

    async def run(self, goal) -> object:
        return await self.loop.run(goal)

    # ---- 常驻协作内核：7×24 无间断运作 ----

    async def serve(self) -> None:
        """启动常驻协作内核主循环（7×24 待机 + 事件驱动唤醒）。

        无请求时挂起待机，不占 CPU、不主动造活；收到 submit 才执行。
        需先经 ``bootstrap_resident`` 装配 ResidentKernel。
        """
        if self.resident is None:
            raise RuntimeError("未装配常驻内核：请用 Kernel.bootstrap_resident 创建")
        await self.resident.serve()

    def submit(self, goal, *, reason) -> None:
        """向常驻内核点名投递一个协作请求（reason 必填：子 agent 独立大脑）。"""
        if self.resident is None:
            raise RuntimeError("未装配常驻内核：请用 Kernel.bootstrap_resident 创建")
        self.resident.submit(goal, reason=reason)

    async def shutdown(self) -> None:
        """优雅关闭常驻内核：停止接收新请求，排空在途任务后退出。"""
        if self.resident is not None:
            await self.resident.shutdown()

    @classmethod
    def bootstrap(
        cls,
        *,
        targets: dict,
        aspects: Iterable[Aspect] = (),
        reason: ReasonProvider | None = None,
    ) -> "Kernel":
        """便捷装配：把 ``{target: handler}`` 能力与护栏组装成可用内核。

        ``targets`` 的值可以是 ``handler`` 或 ``(handler, meta)`` 二元组，
        后者用于登记能力描述（汇入可呼叫清单，供 Agent 决策者发现）。
        """
        bus = EventBus()
        for target, spec in targets.items():
            handler, meta = spec if isinstance(spec, tuple) else (spec, None)
            bus.on(target, handler, meta=meta)
        for aspect in aspects:
            bus.add_aspect(aspect)
        loop = AgentLoop(bus, reason, input_nodes=cls._input_nodes())
        return cls(bus, loop)

    @classmethod
    def bootstrap_resident(
        cls,
        *,
        targets: dict,
        aspects: Iterable[Aspect] = (),
        reason: ReasonProvider | None = None,
        max_concurrency: int = 4,
        input_nodes: Optional[list] = None,
    ) -> "Kernel":
        """便捷装配一个可 7×24 常驻运作的协作内核。

        在 ``bootstrap`` 基础上挂载 ``ResidentKernel``：常驻待机、事件驱动唤醒、
        复用 AgentLoop 执行协作请求。调用方通过 ``serve()`` 启动、``submit`` 投递、
        ``shutdown()`` 优雅关闭。
        """
        kernel = cls.bootstrap(targets=targets, aspects=aspects, reason=reason)
        kernel.resident = ResidentKernel(
            kernel.bus,
            kernel.loop.reason,
            max_concurrency=max_concurrency,
            input_nodes=input_nodes if input_nodes is not None else cls._input_nodes(),
        )
        return kernel

    @staticmethod
    def _input_nodes():
        """从 capabilities.yaml 读取决策输入节点（含 slot）；无配置则用默认。"""
        from ..plugins.loader import CapabilityLoader
        nodes = CapabilityLoader.load_input_nodes()
        return nodes or [{"target": "context", "slot": "user"},
                         {"target": "prompt", "slot": "system"}]


__all__ = [
    "Kernel",
    "EventBus",
    "Aspect",
    "AgentLoop",
    "ReasonProvider",
    "Dispatch",
    "Notice",
    "CapabilityResult",
    "CorrelationId",
    "Scope",
    "ResidentKernel",
]
