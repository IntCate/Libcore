"""新增横切面测试：全局超时（TimeoutAspect）。

验证生产就绪缺口：全局超时做成横切面（仿 budget_guard），任务超时强制结束。

注：重试机制经架构核实后确认**不能做成横切面**（after 返回值被总线丢弃，无法回传重试结果），
正确归属是能力门面层（tools/mcp 门面实际调用工具处），故本文件只测超时。
"""
from __future__ import annotations

import asyncio

from libcore.kernel import Kernel
from libcore.kernel.bus import CapabilityResult, Dispatch
from libcore.kernel.agent.spi import Action, ReasonProvider
from libcore.kernel.bus import Scope
from libcore.plugins.aspects.timeout import TimeoutAspect


class ScriptedReason(ReasonProvider):
    def __init__(self, actions):
        self._actions = list(actions)

    async def decide(self, ctx: Scope) -> Action:
        return self._actions.pop(0) if self._actions else Action(finish=True)


def test_timeout_aspect_forces_done_after_timeout():
    """任务运行超过 timeout 秒，TimeoutAspect 强制 ctx.done=True。"""
    async def slow_handler(d: Dispatch) -> CapabilityResult:
        await asyncio.sleep(0.02)
        return CapabilityResult(ok=True, data={"echo": d.payload})

    kernel = Kernel.bootstrap(
        targets={"echo": slow_handler},
        aspects=[TimeoutAspect(timeout=0.05)],
        reason=ScriptedReason([Action(target="echo", op="run", payload={})] * 10),
    )

    async def run():
        ctx = await kernel.loop.run("任务")
        assert ctx.done is True
        assert any("超时" in str(o) for o in ctx.observations)

    asyncio.run(run())


def test_timeout_aspect_does_not_trigger_within_budget():
    """任务在 timeout 内完成，TimeoutAspect 不干预。"""
    kernel = Kernel.bootstrap(
        targets={"echo": lambda d: CapabilityResult(ok=True, data={"echo": d.payload})},
        aspects=[TimeoutAspect(timeout=10.0)],
        reason=ScriptedReason([Action(target="echo", op="run", payload={})]),
    )

    async def run():
        ctx = await kernel.loop.run("任务")
        assert ctx.done is True
        assert not any("超时" in str(o) for o in ctx.observations)

    asyncio.run(run())
