"""Agent 调度环：reason → dispatch → observe 的闭环。

编排的唯一决策点在此：每轮由 ReasonProvider 推理出"下一步点名谁"，
把 Action 转为带 target 的 Dispatch 发到总线，观察结果后进入下一轮，
直到决策者宣告 finish，或触发护栏上限（m顶步骤）。
"""
from __future__ import annotations

from typing import Any

import asyncio

from ..core.bus import EventBus
from ..core.scope import Scope
from ..core.event import Dispatch, CorrelationId
from .spi import ReasonProvider


class AgentLoop:
    """调度环。构造一次，每次 run 复用（不自建资源）。"""

    def __init__(self, bus: EventBus, reason: ReasonProvider) -> None:
        self.bus = bus
        self.reason = reason

    async def run(self, goal: Any) -> Scope:
        ctx = Scope(goal=goal)
        waits = 0
        while not ctx.done:
            ctx.refresh(self.bus.manifest())  # 每轮注入最新可呼叫清单
            if len(ctx.observations) >= ctx.max_steps:
                ctx.done = True
                ctx.observations.append({"error": "迭代上限达成（护栏）"})
                break
            action = await self.reason.decide(ctx)
            if action.finish:
                ctx.done = True
                break
            if action.wait:  # 本轮无目标，稍等后回到轮顶刷新清单
                waits += 1
                if waits >= 50:
                    ctx.done = True
                    ctx.observations.append({"error": "等待能力上线超时（护栏）"})
                    break
                await asyncio.sleep(0.02)
                continue
            result = await self.bus.dispatch(Dispatch(
                target=action.target,
                op=action.op,
                payload=action.payload,
                cid=ctx.new_cid(),
                parent_cid=ctx.root_cid,
            ))
            ctx.observe(result)
        return ctx