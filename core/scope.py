"""上下文作用域：一次 Agent 会话的可变状态 + 因果链 + 观测累积。

迁移自旧 ``state_bag`` 的概念，但契约更薄：
- 持有 goal、累积能力结果、因果链；
- 由 AgentLoop 驱动观察，作为每次决策注入 ReasonProvider 的输入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .event import CorrelationId, CapabilityResult


@dataclass
class Scope:
    """一次会话的作用域。构造时产生根因果 id。"""

    goal: Any
    max_steps: int = 30
    done: bool = False

    root_cid: CorrelationId = field(default_factory=CorrelationId)
    observations: List[dict] = field(default_factory=list)
    capabilities: List[dict] = field(default_factory=list)  # 可呼叫清单（每轮刷新注入）

    def refresh(self, manifest: List[dict]) -> None:
        """每轮决策前注入最新可呼叫清单，供 ReasonProvider 据此选择 target。"""
        self.capabilities = manifest

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