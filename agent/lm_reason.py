"""LLM 决策者（参考实现）—— 这就是"把 LLM 适配进框架"的全部。

框架核心契约只认 `async def decide(ctx) -> Action`（一张窄接口）。
本文件是一个示例实现：通过极窄的 ChatBackend 门面调用任意 LLM，
把模型的返回翻译成 Action（填"答题卡"）。

== 它如何"适配你的 LLM"，让你不用改框架 ==
1. 装配时把你项目中已有的 ILLMProvider 实例包成一个 ChatBackend：
       backend = from_provider(provider, credentials=creds)
   只需这一行；libcore 内核始终不认识具体模型 / 官方库 / langchain。
2. 换库（官方 SDK / langchain / 自研裸调），只改"提供 backend 的那一行"，
   decide 与内核循环零改动。

不变量：本模块顶部零业务 import（不 import 旧 orchestrator 包），仅依赖标准库 + libcore 自身；
对接旧 provider 的适配放在 from_provider(懒加载) 内，保持内核干净。LM-6 零 langchain_ 引用。
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Protocol

from .spi import Action, ReasonProvider
from ..core.scope import Scope


# ---------------------------------------------------------------
# 1. 极窄 LLM 门面（Protocol）：与项目现有 ILLMProvider.chat 对齐，
#    但不 import 旧包，运行时由装配层注入任意实现。
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# 2. LLM 决策者：ctx → prompt → 模型 → 翻译成 Action（答题卡 3 格）
# ---------------------------------------------------------------

class LlmReasonProvider(ReasonProvider):
    """让模型用"工具调用"直接帮我们填答题卡的参考实现。"""

    def __init__(
        self,
        backend: ChatBackend,
        *,
        model: str,
        system_prompt: str = "你是调度员：看清单决定下一步点名谁，全都做完就收尾。",
        temperature: float = 0.2,
    ) -> None:
        self._backend = backend
        self._model = model
        self._system = system_prompt
        self._temperature = temperature

    async def decide(self, ctx: Scope) -> Action:
        # 1) 把"当前能调用谁"变成模型可选的工具（答题卡上的候选点名对象）
        tools = [
            {
                "type": "function",
                "function": {
                    "name": cap["target"],                 # 例如 tool.pdf
                    "description": cap.get("description", ""),
                    "parameters": {"type": "object", "properties": {
                        "op": {"type": "string"},
                    }},
                },
            }
            for cap in ctx.capabilities
        ]

        # 2) 问模型：给它"现状 + 可呼叫清单"
        result = await self._backend.chat(
            model=self._model,
            messages=[
                LLMMsg("system", self._system),
                LLMMsg("user", self._prompt(ctx)),
            ],
            options={"tools": tools, "temperature": self._temperature},
        )

        # 3) 翻译成答题卡：模型选中一个工具调用 → 就是"下一步点名谁"
        if result.tool_calls:
            call = result.tool_calls[0]
            args = dict(call.arguments)
            op = str(args.pop("op", "run"))
            return Action(target=call.name, op=op, payload=args)

        # 4) 没选工具 → 模型说"做完了"（或该停了）
        return Action(finish=True)

    def _prompt(self, ctx: Scope) -> str:
        return (
            f"当前任务：{_json.dumps(ctx.summary(), ensure_ascii=False)}\n"
            f"可调用能力：{_json.dumps(ctx.capabilities, ensure_ascii=False)}\n"
            "请选择下一步调用的能力（op）、参数；若任务已结束则不要调用任何工具。"
        )


# ---------------------------------------------------------------
# 3. 便捷适配：把项目现有的 ILLMProvider 实例包成 ChatBackend
#    懒加载 import，避免把旧 orchestrator 包拖进内核进程。
# ---------------------------------------------------------------
_FROM_PROVIDER: Callable[[Any, Any], Awaitable[ChatResult]] | None = None


def from_provider(provider: Any, *, credentials: Any) -> ChatBackend:
    """把现有 ILLMProvider 实例包成 ChatBackend（一行适配，不 import 旧代码到模块顶部）。

    用法：
        backend = from_provider(provider, credentials=creds)   # creds 来自你的凭据层
    """
    from ._provider_bridge import build_from_provider  # 懒加载旧包桥

    return build_from_provider(provider, credentials)