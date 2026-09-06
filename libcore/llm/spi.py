"""ChatBackend 契约 —— libcore 与任意 LLM 之间的唯一门面。

极简契约（只要求一个 chat 方法）：
    async def chat(*, model, messages, options) -> ChatResult

官方 SDK / langchain / 自研裸调都能提供它。换库只改"提供 backend 的那一行"，
内核（loop / lm_reason）与具体模型 / 官方库零耦合。

本文件零业务 import（不 import 旧 orchestrator 包），仅依赖标准库。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol


class ChatBackend(Protocol):
    """只要求一个 chat 方法。官方库 / langchain / 自研裸调都能提供它。"""

    async def chat(
        self,
        *,
        model: str,
        messages: List["LLMMsg"],
        options: Dict[str, Any],
    ) -> "ChatResult": ...


@dataclass
class LLMMsg:
    """极简消息（薄，避免把旧 spi 类型带进内核）。"""
    role: str    # "system" / "user" / "assistant"
    content: str


@dataclass
class ToolCall:
    """模型选中的一次工具调用 = 一次点名。"""
    name: str                                   # 例如 "tool.pdf"
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResult:
    """极简响应（text 与 tool_calls 二选一，或都没有）。"""
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
