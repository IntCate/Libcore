"""wait_timeout 归属验证测试：证明 wait 超时治理可做成横切面（与 budget_guard 同构）。

验证主张：
1. WaitTimeoutAspect 监听 loop.iteration，计数 wait action，超限 ctx.done=True；
2. 内核无需改动——AgentLoop 保持纯调度，wait 治理由横切面承担；
3. 与 budget_guard 完全同构（都是"监听 loop.iteration + 计数 + 治理循环"）；
4. 内核不再内置 wait 计数（AgentLoop.run 无 waits 硬编码）。
"""
from __future__ import annotations

import asyncio
import inspect

from libcore.kernel import Kernel, EventBus, AgentLoop
from libcore.kernel.bus import Aspect, CapabilityResult, Dispatch, Notice
from libcore.kernel.agent.spi import Action, ReasonProvider
from libcore.kernel.bus import Scope
from libcore.plugins.aspects.wait_timeout import WaitTimeoutAspect


class WaitReason(ReasonProvider):
    """一直返回 wait，模拟"能力未上线，决策者反复等待"。"""

    def __init__(self, n_waits: int):
        self._n = n_waits

    async def decide(self, ctx: Scope) -> Action:
        if self._n > 0:
            self._n -= 1
            return Action(wait=True)
        return Action(finish=True)


def test_wait_timeout_can_be_a_crosscutting_aspect():
    """wait 超时做成横切面（仿 budget_guard），内核无需改动。"""
    aspect = WaitTimeoutAspect(max_waits=3)
    kernel = Kernel.bootstrap(
        targets={},
        aspects=[aspect],
        reason=WaitReason(n_waits=10),  # 决策者想等 10 次，但横切面 3 次就熔断
    )

    async def run():
        ctx = await kernel.loop.run("任务")
        assert ctx.done is True
        assert any("等待能力上线超时" in str(o) for o in ctx.observations)
        assert sum(aspect._waits.values()) == 3  # 横切面在 3 次 wait 后熔断

    asyncio.run(run())


def test_wait_timeout_aspect_isomorphic_to_budget_guard():
    """WaitTimeoutAspect 与 BudgetGuardAspect 同构：都监听 loop.iteration + 计数 + 治理。"""
    from libcore.plugins.aspects.budget_guard import BudgetGuardAspect

    wt = WaitTimeoutAspect(max_waits=3)
    bg = BudgetGuardAspect(max_iterations=25)

    # 同构验证：都匹配 loop.iteration 广播
    sig = Notice(topic="loop.iteration", payload={})
    assert wt.matches(sig) is True
    assert bg.matches(sig) is True
    # 都通过 ctx.done 治理循环
    assert hasattr(wt, "_waits")
    assert hasattr(bg, "_used")


def test_kernel_no_longer_builtin_wait_counter():
    """内核不再内置 wait 计数：AgentLoop.run 源码不含 waits 硬编码护栏。"""
    src = inspect.getsource(AgentLoop.run)
    assert "waits" not in src
    assert "等待能力上线超时" not in src
