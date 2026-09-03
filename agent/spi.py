"""决策提供者 SPI：Agent 如何决定"下一步点名谁"。

内核只依赖这个接口，不关心实际决策者是谁——
可以是 LLM 推理、脚本计划、规则表，任意替换。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict

from ..core.scope import Scope


@dataclass(frozen=True)
class Action:
    """Agent 一轮决策的产品：点名一个能力、等待、或结束。"""

    target: str = ""
    op: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    finish: bool = False
    wait: bool = False   # True = 本轮无可调用目标，请调度环稍后重试（配合每轮清单刷新）


class ReasonProvider(ABC):
    """决策接口：观察当前 Scope，返回下一步 Action。"""

    @abstractmethod
    async def decide(self, ctx: Scope) -> Action:
        """基于 ctx.summary() 决定下一个被点名的能力，或宣告完成。"""