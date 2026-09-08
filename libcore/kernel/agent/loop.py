"""Agent 调度环：reason → dispatch → observe 的闭环。

编排的唯一决策点在此：每轮由 ReasonProvider 推理出"下一步点名谁"，
把 Action 转为带 target 的 Dispatch 发到总线，观察结果后进入下一轮，
直到决策者宣告 finish，或触发护栏上限（m顶步骤）。
"""
from __future__ import annotations

from typing import Any, Optional

import asyncio

from ..bus import EventBus, Scope
from ..bus import Dispatch, Notice, CorrelationId, NoHandlerError
from .spi import ReasonProvider
from ...llm.spi import LLMMsg


class AgentLoop:
    """调度环。构造一次，每次 run 复用（不自建资源）。

    ``input_nodes`` 是"决策输入节点"配置驱动的 target 列表（方向 Y）：
    - 内核不硬编码任何具体输入节点名，只负责在每轮决策前逐个调用、无感降级并聚合；
    - 默认 None → 退化为内置的 ``("context", "prompt")``；可配置为任意节点（如加 ``memory``）。
    加一个输入节点只改配置/传参，内核零改动。
    """

    def __init__(self, bus: EventBus, reason: ReasonProvider,
                 input_nodes: Optional[list] = None) -> None:
        self.bus = bus
        self.reason = reason
        # 决策输入节点：配置驱动（含 slot）。input_nodes 元素可为 str（默认 slot=user）
        # 或 dict（{"target":..., "slot":...}）。默认退化为内置 ("context","prompt")。
        self._input_nodes = self._normalize_input_nodes(input_nodes)

    @staticmethod
    def _normalize_input_nodes(input_nodes: Optional[list]) -> List[dict]:
        """把 input_nodes 归一化为 [{target, slot, trim?}] 列表。

        - 元素为 str → {"target": s, "slot": "user"}
        - 元素为 dict → 取 target/slot/trim（slot 缺省 "user"，trim 缺省 None）
        - None → 默认 [{"target":"context","slot":"user"}, {"target":"prompt","slot":"system"}]
        """
        if input_nodes is None:
            return [
                {"target": "context", "slot": "user"},
                {"target": "prompt", "slot": "system"},
            ]
        out: List[dict] = []
        for item in input_nodes:
            if isinstance(item, str):
                out.append({"target": item, "slot": "user"})
            else:
                out.append({"target": item["target"], "slot": item.get("slot", "user"),
                            "trim": item.get("trim")})
        return out

    async def run(self, goal: Any, *, session_id: Optional[str] = None) -> Scope:
        ctx = Scope(goal=goal, session_id=session_id)
        while not ctx.done:
            ctx.refresh(self.bus.manifest())  # 每轮注入最新可呼叫清单
            await self._prepare_input(ctx)    # 决策前无感注入 prompt/context 强化片段
            action = await self.reason.decide(ctx)
            # 每轮决策后广播 loop.iteration，供治理横切面（预算/死循环）维护跨轮次状态
            await self.bus.publish(Notice(topic="loop.iteration",
                                          payload={"ctx": ctx, "action": action}))
            if ctx.done:  # 治理横切面（预算熔断/死循环）已标记结束
                break
            if action.finish:
                ctx.done = True
                await self.bus.publish(Notice(topic="loop.finish",
                                              payload={"goal": goal}))
                break
            if action.wait:  # 本轮无目标，稍等后回到轮顶刷新清单（超时治理由 wait_timeout 横切面承担）
                await asyncio.sleep(0.02)
                continue
            result = await self.bus.dispatch(Dispatch(
                target=action.target,
                op=action.op,
                payload=action.payload,
                cid=ctx.new_cid(),
                parent_cid=ctx.root_cid,
            ))
            # 决策后广播 loop.result，供治理横切面（死循环/追踪）拿到本轮真实结果
            await self.bus.publish(Notice(topic="loop.result",
                                          payload={"ctx": ctx, "action": action,
                                                   "result": result}))
            # 决策后追加工具调用/结果消息，让模型看到"调了什么 → 得到什么"（决策后累积）
            ctx.add_input("_tool", [
                LLMMsg("assistant", f"调用 {action.target}.{action.op}"),
                LLMMsg("user", f"结果：{result.data if result.ok else result.error}"),
            ], slot="user")
            ctx.observe(result)
        return ctx

    async def _prepare_input(self, ctx: Scope) -> None:
        """决策前无感调用配置驱动的一组输入节点（方向 Y：统一聚合 + 降级）。

        - 遍历 ``self._input_nodes``（默认 ``["context","prompt"]``，可配置加 ``memory`` 等）；
        - 有节点（注册了该 target）→ 取回消息序列经 ``ctx.add_input(target, msgs)`` 聚合；
        - 无节点（点名落空 NoHandlerError）→ 静默降级，该 target 缺席，决策者用最简默认。
        不报错、不阻塞，保证"没有是基本运行，有是强化"。
        """
        for node in self._input_nodes:
            target = node["target"]
            try:
                res = await self.bus.dispatch(Dispatch(
                    target=target, op="run",
                    payload={"goal": ctx.goal, "session_id": ctx.session_id,
                             "trim": node.get("trim")},
                    cid=ctx.new_cid(),
                    parent_cid=ctx.root_cid,
                ))
            except NoHandlerError:
                # 无节点：降级为最简默认，不报错；但广播 input_missing 让降级可被观测
                # （避免"插件没注册"被静默掩盖，开发者能感知配置错误）
                await self.bus.publish(Notice(
                    topic="loop.input_missing",
                    payload={"target": target, "goal": ctx.goal},
                ))
                continue
            if res.ok and isinstance(res.data, dict):
                msgs = res.data.get("messages")
                if isinstance(msgs, list) and msgs:
                    # 硬约束：元素必须是 LLMMsg，否则明确报错（避免开发者传的格式与底层不一致）
                    if not all(isinstance(m, LLMMsg) for m in msgs):
                        raise TypeError(
                            f"输入节点 {target} 产出 messages 元素必须是 LLMMsg，"
                            f"实际含 {type(msgs[0]).__name__}"
                        )
                    ctx.add_input(target, msgs, slot=node["slot"])
