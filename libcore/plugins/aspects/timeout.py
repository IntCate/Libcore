"""横切面插件：全局超时护栏（新增，生产就绪缺口）。

监听 ``loop.iteration`` 广播（每轮决策后由 AgentLoop 发出），记录任务开始时间，
超过 ``timeout`` 秒后把 ``ctx.done`` 置 True 治理循环，并广播 ``loop.guard``。

与 budget_guard 同模式：都是"监听 loop.iteration + 维护跨轮次状态 + ctx.done 治理"。
区别：budget_guard 按"迭代次数"，本护栏按"墙钟时间"。
"""
from __future__ import annotations

import time

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Notice


class TimeoutAspect(Aspect):
    """全局超时护栏：任务运行超过 timeout 秒即强制结束。"""

    def __init__(self, timeout: float = 300.0, bus=None):
        self.timeout = timeout
        self._bus = bus
        self._start: float | None = None

    def matches(self, signal) -> bool:
        return isinstance(signal, Notice) and signal.topic == "loop.iteration"

    async def before(self, signal):
        ctx = (signal.payload or {}).get("ctx")
        if ctx is None:
            return None
        if self._start is None:
            self._start = time.monotonic()
            return None
        if time.monotonic() - self._start > self.timeout:
            ctx.done = True
            ctx.observations.append({"error": f"任务超时（{self.timeout}s），自动停止。"})
            if self._bus is not None:
                await self._bus.publish(Notice(
                    topic="loop.guard",
                    payload={"reason": "timeout", "goal": ctx.goal},
                ))
        return None

    def reset(self) -> None:
        self._start = None


def register(bus, timeout: float = 300.0) -> None:
    """注册全局超时护栏。``timeout`` 可由 aspects.yaml 的 ``config`` 覆盖（缺省 300s）。"""
    bus.add_aspect(TimeoutAspect(timeout=timeout, bus=bus))
