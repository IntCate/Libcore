"""对接项目现有 LLM provider 层的桥（纯懒加载，避免拖进内核脏依赖）。

说明：
    libroce/agent/lm_reason.py 顶部零业务 import；只有当用户调用
    `from_provider(...)` 时，本模块才 import 旧的 llm_node_vendors.spi，
    把现有 ILLMProvider 实例翻译成 lm_reason.ChatBackend。
    这样内核(loop/bus/core)从不 import 旧 orchestrator 包。
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..agent.lm_reason import ChatResult, LLMMsg, ToolCall

# 旧 provider SPI 在这里懒加载（仅在函数内可见，不进模块命名空间）
def _import_spi():
    from app.runtime.kernel.orchestrator.llm_node_vendors.spi import (
        LLMMessage as RealMsg,
        ProviderCallOptions,
        Role,
        TextContent,
        ToolCallContent,
    )
    return RealMsg, ProviderCallOptions, Role, TextContent, ToolCallContent


def build_from_provider(provider: Any, credentials: Any):
    """返回一个 ChatBackend，把现有 ILLMProvider.chat 的返回翻译成 lm_reason 能读的结果。"""
    RealMsg, ProviderCallOptions, Role, TextContent, ToolCallContent = _import_spi()

    async def chat(
        *,
        model: str,
        messages: List[LLMMsg],
        options: Dict[str, Any] | None = None,
    ) -> ChatResult:
        real_messages = [RealMsg(role=Role(m.role), content=m.content) for m in messages]
        resp = await provider.chat(
            model=model,
            messages=real_messages,
            options=ProviderCallOptions(**(options or {})),
            credentials=credentials,
        )
        calls: List[ToolCall] = []
        text = ""
        for block in resp.content:
            if isinstance(block, ToolCallContent):
                calls.append(ToolCall(name=block.name, arguments=dict(block.arguments)))
            elif isinstance(block, TextContent):
                text += block.text
        return ChatResult(content=text, tool_calls=calls)

    return chat