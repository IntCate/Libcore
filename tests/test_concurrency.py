"""并发安全测试：横切面单例在并发任务下是否互相污染。

生产级关键隐患：横切面是挂在总线上的**单例**，其跨轮次状态
（budget_guard.used / loop_governor._history / circuit_breaker._fails /
trace_recorder._start / telemetry._start 等）是共享可变状态。
ResidentKernel 用 Semaphore 支持并发，多个 AgentLoop 并发跑同一总线时，
这些状态若按"全局"而非"按会话/信号"隔离，会互相污染。

验证主张：
1. 观察类横切面（tracing/telemetry/logging）用 cid 作 key 隔离 → 并发安全；
2. 治理类横切面（budget_guard/loop_governor）的跨轮次计数若按全局累计，
   并发任务会互相触发熔断（任务 A 的迭代数算到任务 B 头上）。
"""
from __future__ import annotations

import asyncio

from libcore.kernel.bus import EventBus, Dispatch, Notice, Aspect, CapabilityResult
from libcore.kernel.agent.spi import Action, ReasonProvider
from libcore.kernel.bus import Scope
from libcore.plugins.aspects.budget_guard import BudgetGuardAspect
from libcore.plugins.aspects.tracing import TracingAspect
from libcore.plugins.aspects.telemetry import TelemetryAspect


class CountingReason(ReasonProvider):
    """每轮点名一个计数工具，n 轮后 finish。"""

    def __init__(self, n_rounds: int):
        self._n = n_rounds

    async def decide(self, ctx: Scope) -> Action:
        if self._n > 0:
            self._n -= 1
            return Action(target="counter", op="inc", payload={})
        return Action(finish=True)


def _counter_handler(d: Dispatch) -> CapabilityResult:
    return CapabilityResult(ok=True, data={"n": 1})


def test_observer_aspects_are_concurrency_safe():
    """观察类横切面（tracing/telemetry）用 cid 隔离，并发下不串数据。"""
    bus = EventBus()
    tracing = TracingAspect()
    telemetry = TelemetryAspect()
    bus.add_aspect(tracing)
    bus.add_aspect(telemetry)
    bus.on("counter", _counter_handler)

    async def run_one(goal):
        from libcore.kernel.agent.loop import AgentLoop
        loop = AgentLoop(bus, CountingReason(n_rounds=3))
        await loop.run(goal)

    async def main():
        await asyncio.gather(run_one("A"), run_one("B"), run_one("C"))

    asyncio.run(main())
    # 3 个任务 × 3 轮 = 9 次 dispatch，全部被 tracing 记录（tracing 只匹配 Dispatch）
    assert len(tracing.records) == 9, f"应追踪 9 条，实际 {len(tracing.records)}"
    # telemetry 匹配所有信号（dispatch + notice），但 dispatch 类 span 应为 9
    dispatch_spans = [s for s in telemetry.spans if s["kind"] == "dispatch"]
    assert len(dispatch_spans) == 9, f"应遥测 9 条 dispatch，实际 {len(dispatch_spans)}"
    # 每个 dispatch 的 cid 唯一（并发不串）
    cids = [r["cid"] for r in tracing.records]
    assert len(set(cids)) == 9, "并发下 cid 应唯一"


def test_budget_guard_global_counter_pollutes_across_tasks():
    """治理类横切面（budget_guard）按全局累计 used，并发任务会互相触发熔断。

    这是生产级隐患：任务 A 的迭代数算到任务 B 头上，导致任务 B 被提前熔断。
    """
    bus = EventBus()
    bg = BudgetGuardAspect(max_iterations=5)
    bus.add_aspect(bg)
    bus.on("counter", _counter_handler)

    async def run_one(goal, n_rounds):
        from libcore.kernel.agent.loop import AgentLoop
        loop = AgentLoop(bus, CountingReason(n_rounds=n_rounds))
        ctx = await loop.run(goal)
        return ctx

    async def main():
        # 两个任务各 3 轮，若 used 全局共享，第 2 个任务跑到第 2 轮就累计到 5 被熔断
        ctx_a = await run_one("A", 3)
        ctx_b = await run_one("B", 3)
        return ctx_a, ctx_b

    ctx_a, ctx_b = asyncio.run(main())
    # 若 used 全局共享：ctx_b 会在第 2 轮被熔断（3+2=5），done 提前为 True
    # 若按任务隔离：ctx_b 应完整跑完 3 轮，done 由 finish 触发
    assert ctx_b.done is True
    # 关键断言：任务 B 不应因任务 A 的迭代数被提前熔断
    # 若被污染，ctx_b 的 observations 会含"已达到最大迭代次数"
    polluted = any("已达到最大迭代次数" in str(o) for o in ctx_b.observations)
    assert not polluted, "budget_guard 全局计数污染了并发任务 B"
