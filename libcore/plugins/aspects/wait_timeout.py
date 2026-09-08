"""横切面插件：等待能力上线超时护栏（从内核内置迁移到横切面）。

原为 AgentLoop.run 内置的 ``waits >= 50`` 硬编码护栏。按"内核=纯调度器、
横切面=治理层"的分层主张迁移为横切面，与 budget_guard / timeout 同构：
- 匹配 ``loop.iteration`` 广播（每轮决策后由 AgentLoop 发出）；
- 维护跨轮次 wait 计数，超过 ``max_waits`` 后把 ``ctx.done`` 置 True 治理循环，
  并广播 ``loop.guard``（reason=wait_timeout），让日志横切面与订阅者都能感知。

区别：budget_guard 按"迭代次数"、timeout 按"墙钟时间"、本护栏按"连续 wait 次数"。
"""
from __future__ import annotations

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Notice


class WaitTimeoutAspect(Aspect):
    """等待能力上线超时护栏：连续 wait 超过 max_waits 次即强制结束。

    跨轮次 wait 计数按会话（ctx.root_cid）隔离：并发任务各自独立计数，
    互不污染（生产级并发安全）。
    """

    def __init__(self, max_waits: int = 50, bus=None):
        self.max_waits = max_waits
        self._waits: dict[str, int] = {}   # ctx.root_cid -> 该会话连续 wait 次数
        self._bus = bus

    def matches(self, signal) -> bool:
        return isinstance(signal, Notice) and signal.topic == "loop.iteration"

    async def before(self, signal):
        ctx = (signal.payload or {}).get("ctx")
        action = (signal.payload or {}).get("action")
        if ctx is None or action is None:
            return None
        if not getattr(action, "wait", False):
            return None
        key = ctx.root_cid.value
        waits = self._waits.get(key, 0) + 1
        self._waits[key] = waits
        if waits >= self.max_waits:
            ctx.done = True
            ctx.observations.append({"error": "等待能力上线超时（横切面护栏）"})
            if self._bus is not None:
                await self._bus.publish(Notice(
                    topic="loop.guard",
                    payload={"reason": "wait_timeout", "goal": ctx.goal},
                ))
        return None

    def reset(self) -> None:
        self._waits.clear()


def register(bus, max_waits: int = 50) -> None:
    """注册等待超时护栏。``max_waits`` 可由 aspects.yaml 的 ``config`` 覆盖（缺省 50）。"""
    bus.add_aspect(WaitTimeoutAspect(max_waits=max_waits, bus=bus))
