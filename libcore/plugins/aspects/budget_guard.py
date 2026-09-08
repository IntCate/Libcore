"""横切面插件：迭代预算护栏（原生重写，不 import 旧 app.runtime）。

由旧架构 ``IterationBudget``（三级压力注入）重写而来，但按 libcore 收敛：
- 匹配 ``loop.iteration`` 广播（每轮决策前由 AgentLoop 发出）；
- 维护跨轮次迭代计数，按 70%/90%/100% 三级压力注入；
- 100% 时把 ``ctx.done`` 置 True 治理循环（护栏熔断），并广播 ``loop.guard``。

压力消息注入到 ``ctx.input_fragments["budget"]``（消息序列），决策者"看到"后自我收敛。
"""
from __future__ import annotations

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Notice
from libcore.llm.spi import LLMMsg


class BudgetGuardAspect(Aspect):
    """迭代预算护栏：三级压力注入 + 100% 熔断。

    跨轮次计数按会话（ctx.root_cid）隔离：并发任务各自独立计数，
    互不污染（生产级并发安全）。
    """

    def __init__(self, max_iterations: int = 25, bus=None):
        self.max = max_iterations
        self._used: dict[str, int] = {}   # ctx.root_cid -> 该会话已用迭代数
        self._bus = bus

    def matches(self, signal) -> bool:
        return isinstance(signal, Notice) and signal.topic == "loop.iteration"

    async def before(self, signal):
        ctx = (signal.payload or {}).get("ctx")
        if ctx is None:
            return None
        key = ctx.root_cid.value
        used = self._used.get(key, 0) + 1
        self._used[key] = used
        pct = used / self.max

        if pct >= 1.0:
            ctx.done = True
            ctx.observations.append({"error": f"已达到最大迭代次数（{self.max}），自动停止。"})
            # 广播 loop.guard，让日志横切面与订阅者都能感知"预算熔断"（与 wait_timeout 护栏一致）
            if self._bus is not None:
                await self._bus.publish(Notice(
                    topic="loop.guard",
                    payload={"reason": "budget_guard", "goal": ctx.goal},
                ))
            return None

        pressure = None
        if pct >= 0.9:
            remaining = self.max - used
            pressure = (
                f"\n\n[BUDGET WARNING: Iteration {used}/{self.max}. "
                f"Only {remaining} left. Provide your final response NOW.]"
            )
        elif pct >= 0.7:
            remaining = self.max - used
            pressure = (
                f"\n\n[BUDGET: Iteration {used}/{self.max}. "
                f"{remaining} left. Start consolidating your findings.]"
            )
        if pressure:
            ctx.add_input("budget", [LLMMsg("system", pressure)], slot="system")
        return None

    def reset(self) -> None:
        self._used.clear()


def register(bus, max_iterations: int = 25) -> None:
    """注册迭代预算护栏。``max_iterations`` 可由 aspects.yaml 的 ``config`` 覆盖（缺省 25）。"""
    bus.add_aspect(BudgetGuardAspect(max_iterations=max_iterations, bus=bus))
