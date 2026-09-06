"""上下文作用域：一次 Agent 会话的可变状态 + 因果链 + 观测累积。

迁移自旧 ``state_bag`` 的概念，但契约更薄：
- 持有 goal、累积能力结果、因果链；
- 由 AgentLoop 驱动观察，作为每次决策注入 ReasonProvider 的输入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .event import CorrelationId, CapabilityResult
from ...llm.spi import LLMMsg


@dataclass
class Scope:
    """一次会话的作用域。构造时产生根因果 id。"""

    goal: Any
    max_steps: int = 30
    done: bool = False
    session_id: Optional[str] = None

    root_cid: CorrelationId = field(default_factory=CorrelationId)
    observations: List[dict] = field(default_factory=list)
    capabilities: List[dict] = field(default_factory=list)  # 可呼叫清单（每轮刷新注入）
    # 决策输入聚合区（方向 Y）：内核按配置驱动调用一组输入节点，
    # 每个节点产出消息序列（List[LLMMsg]），按 target 名存入本字典。无节点则保持空。
    # 内核不认任何具体节点名，只做通用聚合。
    input_fragments: Dict[str, List[LLMMsg]] = field(default_factory=dict)
    # 每个输入节点注入的 slot（system/user），由配置驱动；消费端按 slot 折叠。
    input_slots: Dict[str, str] = field(default_factory=dict)

    def refresh(self, manifest: List[dict]) -> None:
        """每轮决策前注入最新可呼叫清单，供 ReasonProvider 据此选择 target。"""
        self.capabilities = manifest

    def add_input(self, target: str, messages: List[LLMMsg], *, slot: str = "user") -> None:
        """按输入节点 target 聚合注入一段消息序列（方向 Y 通用聚合）。

        内核不认具体节点名，只把节点产出的消息序列按 target 名存进聚合区，
        并记录其注入 slot（system/user），供消费端按 slot 折叠。
        """
        if messages:
            self.input_fragments[target] = list(messages)
            self.input_slots[target] = slot

    def new_cid(self) -> CorrelationId:
        """派生一条子因果 id（挂在当前根因果链上）。"""
        return CorrelationId()

    def observe(self, result: CapabilityResult) -> None:
        """记录一次能力执行结果，供下一轮决策参考。"""
        self.observations.append({
            "ok": result.ok,
            "data": result.data,
            "error": result.error,
        })

    def summary(self) -> Dict[str, Any]:
        """给 ReasonProvider 的紧凑上下文。"""
        return {"goal": self.goal, "observations": list(self.observations)}
