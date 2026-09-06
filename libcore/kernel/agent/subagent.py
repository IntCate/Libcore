"""子 agent 与内核 agent：内核 agent 创建子 agent、管理生命周期、回收结果。

角色边界（对齐 agentOS 蓝图）：
- ``KernelAgent``：内核 agent，只做**底层协作编排**（拆解任务 → 创建子 agent →
  派发 → 回收结果），**不做事任务推理**。它本身是一个 ``ReasonProvider``，
  由 AgentLoop 驱动，每轮决定"下一步编排动作"。
- ``SubAgent``：子 agent，由内核 agent 创建，注册为总线节点 ``agent.child.<id>``，
  用**独立 reason 实例**跑子任务，结果回传给内核 agent。

与旧 ``ResidentKernel``（接单机器）的区别：这里"创建子 agent"的是**内核 agent**
（一个会推理的决策者），而不是外部调用方平级塞任务。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from ..bus import EventBus, Dispatch, Notice, CapabilityResult, Scope
from .loop import AgentLoop
from .spi import Action, ReasonProvider

_logger = logging.getLogger("libcore.subagent")


class SubAgent:
    """子 agent：由内核 agent 创建，注册为总线节点，用独立 reason 跑子任务。

    - 注册为 ``agent.child.<id>`` 节点，可被内核 agent 点名（dispatch）派发子任务；
    - 每个子 agent 持有**独立 reason 实例**（独立大脑），互不干扰；
    - 结果经 ``dispatch`` 返回给内核 agent。
    """

    def __init__(
        self,
        bus: EventBus,
        reason: ReasonProvider,
        *,
        agent_id: str,
        input_nodes: Optional[list] = None,
    ) -> None:
        self.bus = bus
        self.reason = reason
        self.agent_id = agent_id
        self.target = f"agent.child.{agent_id}"
        self._input_nodes = input_nodes
        self._loop = AgentLoop(bus, reason, input_nodes=input_nodes)
        self._last_ctx: Optional[Scope] = None

    async def run(self, goal: Any) -> Scope:
        """执行一个子任务，返回该子 agent 的 Scope（含 observations / done）。"""
        self._last_ctx = await self._loop.run(goal)
        return self._last_ctx

    @property
    def last_ctx(self) -> Optional[Scope]:
        return self._last_ctx

    def register(self) -> None:
        """把子 agent 注册为总线节点，供内核 agent 点名派发。"""
        async def handle(d: Dispatch) -> CapabilityResult:
            goal = d.payload.get("goal", d.payload)
            ctx = await self.run(goal)
            return CapabilityResult(ok=ctx.done, data={
                "agent_id": self.agent_id,
                "done": ctx.done,
                "steps": len(ctx.observations),
                "observations": list(ctx.observations),
            })
        self.bus.on(self.target, handle, meta={
            "description": f"子 agent {self.agent_id}：内核 agent 派发子任务给它执行",
            "ops": ["run"],
        })

    def unregister(self) -> None:
        """从总线注销该子 agent 节点（生命周期结束）。"""
        self.bus.off(self.target)


class KernelAgent(ReasonProvider):
    """内核 agent：只做底层协作编排，不做事任务推理、不拆解任务。

    由 AgentLoop 驱动，每轮 ``decide`` 决定一个编排动作：
    - 接收**已拆好的子任务列表**（由任务 agent 拆解后传入）；
    - 为每个子任务创建子 agent（new 独立 reason + 注册为总线节点）；
    - 派发子任务 → 点名子 agent 节点；
    - 回收结果 → 汇总；
    - 全部完成 → finish。

    边界：**拆解任务（用户任务 → 子任务列表）是任务 agent 的职责**，内核 agent
    只消费已拆好的子任务列表，不自己拆解、不持有业务语义。

    子 agent 的构成（含"大脑从哪来"）由 ``sub_factory`` 负责（必填：
    每个子 agent 独立 reason，互不干扰）。
    """

    def __init__(
        self,
        bus: EventBus,
        *,
        sub_factory,
        resource_mapper=None,
        input_nodes: Optional[list] = None,
    ) -> None:
        self.bus = bus
        # sub_factory：创建整个子 agent 的工厂（内部决定子 agent 的构成，含独立 reason）。
        # 必填。开发者定制子 agent（命名 / 类型 / 大脑）只需注入自己的工厂，不必改内核。
        self._sub_factory = sub_factory
        # resource_mapper：子任务 → 资源指令（内核 agent 独有编排决策）。
        # 不传则用默认关键词规则（演示级）；真实场景注入 LLM/规则 mapper。
        self._resource_mapper = resource_mapper or self._default_mapper
        self._input_nodes = input_nodes
        self._children: Dict[str, SubAgent] = {}
        self._results: List[dict] = []
        self._counter = 0

    # ---- 资源编排推理：子任务 → 资源指令（非传话筒的核心）----

    @staticmethod
    def _default_mapper(subtask: Any) -> Dict[str, Any]:
        """默认资源映射：按子任务语义关键词，选能力门面 + 具体工具。

        这是演示级规则；真实场景应注入 ``resource_mapper``（LLM / 规则表），
        由内核 agent 自己的编排推理决定"用哪个资源"。
        """
        text = str(subtask).lower()
        if any(k in text for k in ("待办", "todo", "add", "记录")):
            return {
                "facade": "tools", "tool": "todo", "op": "add",
                "args": {"content": subtask.get("content") if isinstance(subtask, dict) else text,
                         "session": "kernel"},
            }
        if any(k in text for k in ("文件", "file", "写")):
            return {
                "facade": "tools", "tool": "file", "op": "write",
                "args": {"path": "kernel-agent.txt",
                         "content": subtask.get("content") if isinstance(subtask, dict) else str(subtask)},
            }
        return {
            "facade": "tools", "tool": "bash", "op": "run",
            "args": {"command": str(subtask)},
        }

    def _resource_map(self, subtask: Any) -> Dict[str, Any]:
        """资源编排决策：子任务 → 资源指令（选择能力门面 / 工具 / 操作 / 参数）。"""
        spec = self._resource_mapper(subtask)
        if isinstance(spec, dict) and "resource" in spec:
            return spec
        return {"subtask": subtask, "resource": spec}

    # ---- 编排动作：创建子 agent → 派发 → 回收 ----

    def _create_sub(self, goal: Any) -> SubAgent:
        """创建一个子 agent：交给 sub_factory 造（决定子 agent 的构成/命名/大脑）。"""
        self._counter += 1
        sub = self._sub_factory(goal=goal, agent_id=f"sub{self._counter}")
        sub.register()
        self._children[sub.agent_id] = sub
        _logger.info("kernel_agent.create_sub agent_id=%s target=%s", sub.agent_id, sub.target)
        return sub

    async def _dispatch_sub(self, sub: SubAgent, goal: Any) -> CapabilityResult:
        """点名派发子任务给子 agent 节点。"""
        return await self.bus.dispatch(Dispatch(
            target=sub.target,
            op="run",
            payload={"goal": goal},
        ))

    def _reap(self, sub: SubAgent, result: CapabilityResult) -> None:
        """回收子 agent 结果并注销节点（生命周期结束）。"""
        self._results.append({
            "agent_id": sub.agent_id,
            "ok": result.ok,
            "data": result.data,
        })
        sub.unregister()
        self._children.pop(sub.agent_id, None)
        _logger.info("kernel_agent.reap agent_id=%s ok=%s", sub.agent_id, result.ok)

    # ---- 决策：每轮一个编排动作 ----

    async def decide(self, ctx: Scope) -> Action:
        # 只消费任务 agent 已拆好的子任务列表，不自己拆解。
        subtasks = self._subtasks(ctx)
        if not subtasks:
            return Action(finish=True)
        # 逐个创建子 agent 并派发（这里串行演示；可并行）
        for subtask in subtasks:
            # 内核编排决策：子任务 → 资源指令，子 agent 收到的是映射后的指令
            spec = self._resource_map(subtask)
            sub = self._create_sub(spec)
            result = await self._dispatch_sub(sub, spec)
            self._reap(sub, result)
        return Action(finish=True)

    def _subtasks(self, ctx: Scope) -> List[Any]:
        """读取任务 agent 已拆好的子任务列表（不拆解，只消费）。"""
        goal = ctx.goal
        if isinstance(goal, dict):
            subs = goal.get("subtasks")
            if isinstance(subs, list):
                return subs
        return []

    # ---- 状态 ----

    @property
    def children(self) -> Dict[str, SubAgent]:
        return dict(self._children)

    @property
    def results(self) -> List[dict]:
        return list(self._results)
