"""事件原语：两类事件 + 统一因果链。

- ``Dispatch``：点名原语，用于 Agent 业务调度。携带目标地址（target），
  由总线定向直投（O(1) 查表），语义是总线、实现非谓词扫描。
- ``Notice``：广播原语，用于系统级通知 / 感知，按 topic 扇出。
- 每条事件自动挂 ``CorrelationId``，构成全链路因果链（可观测 / 可回放）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from uuid import uuid4


@dataclass(frozen=True)
class CorrelationId:
    """全链路因果 id，随事件自动生成并向下传播。"""

    value: str = field(default_factory=lambda: uuid4().hex)

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return self.value


@dataclass(frozen=True)
class Dispatch:
    """点名原语：Agent 业务调度。``target`` 是信号携带的目标地址。"""

    target: str
    op: str
    payload: Dict[str, Any] = field(default_factory=dict)
    cid: CorrelationId = field(default_factory=CorrelationId)
    parent_cid: Optional[CorrelationId] = None


@dataclass(frozen=True)
class Notice:
    """广播原语：系统级通知 / 感知，按 topic 扇出。"""

    topic: str
    payload: Dict[str, Any] = field(default_factory=dict)
    cid: CorrelationId = field(default_factory=CorrelationId)


@dataclass
class CapabilityResult:
    """被点名能力的执行结果。"""

    ok: bool = True
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class NoHandlerError(Exception):
    """点名落空：总线上没有注册对应 target。"""

    def __init__(self, target: str) -> None:
        super().__init__(f"点名落空：总线未注册 target={target!r}")
        self.target = target