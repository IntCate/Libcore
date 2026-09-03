"""参考适配器：用 langchain-ollama 实现 ChatBackend（三方适配库用法）。

用法：
    backend = LangChainOllamaBackend(model="qwen3:0.6b")
    reason  = LlmReasonProvider(backend, model="qwen3:0.6b")

与 OllamaBackend 完成同一件事，但走 langchain 的 ChatOllama。
一个能跑，另一个换行也行 → 证明框架对 backend 通用。
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..lm_reason import ChatResult, LLMMsg, ToolCall


class LangChainOllamaBackend:
    """用 langchain-ollama（ChatOllama）实现 ChatBackend。"""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://localhost:11434",
        temperature: float = 0.2,
    ) -> None:
        from langchain_ollama import ChatOllama  # 懒加载：不强制安装 langchain

        self._chat = ChatOllama(model=model, base_url=base_url, temperature=temperature)

    async def chat(
        self,
        *,
        model: str,
        messages: List[LLMMsg],
        options: Dict[str, Any],
    ) -> ChatResult:
        from langchain_core.messages import HumanMessage, SystemMessage

        conv = []
        for m in messages:
            cls = SystemMessage if m.role == "system" else HumanMessage
            conv.append(cls(content=m.content))

        bound = self._chat
        if options.get("tools"):
            bound = self._chat.bind_tools(options["tools"])  # dict 形态 tool schema

        resp = await bound.ainvoke(conv)

        calls: List[ToolCall] = []
        for tc in getattr(resp, "tool_calls", []) or []:
            name = tc.get("name") or ""
            args = tc.get("args") or {}
            if name:
                calls.append(ToolCall(name=name, arguments=args))

        text = resp.content if isinstance(resp.content, str) else ""
        return ChatResult(content=text, tool_calls=calls)