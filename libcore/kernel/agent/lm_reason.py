"""LLM 决策者（参考实现）—— 这就是"把 LLM 适配进框架"的全部。

框架核心契约只认 `async def decide(ctx) -> Action`（一张窄接口）。
本文件是一个示例实现：通过极窄的 ChatBackend 门面调用任意 LLM，
把模型的返回翻译成 Action（填"答题卡"）。

== 它如何"适配你的 LLM"，让你不用改框架 ==
1. 装配时把任意 backend 实例（见 libcore/llm/）传给 LlmReasonProvider：
       backend = OpenAICompletionsBackend(api_key=...)
       reason = LlmReasonProvider(backend, model="gpt-4o")
   只需这几行；libcore 内核始终不认识具体模型 / 官方库 / langchain。
2. 换库（官方 SDK / langchain / 自研裸调），只改"提供 backend 的那一行"，
   decide 与内核循环零改动。

不变量：本模块顶部零业务 import（不 import 旧 orchestrator 包），仅依赖标准库 + libcore 自身。
LM-6 零 langchain_ 引用。
"""
from __future__ import annotations

import json as _json
from typing import List

from .spi import Action, ReasonProvider
from ..bus import Scope
from ...llm.spi import ChatBackend, LLMMsg, ToolCall


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
        system_prompt: str | None = None,
        temperature: float = 0.2,
    ) -> None:
        self._backend = backend
        self._model = model
        # 系统指令唯一真相源 = 配置 llm.yaml system_prompt（缺省回退 llm 包内建兜底）
        if system_prompt is None:
            from ...llm import defaults as _llm_defaults

            system_prompt = _llm_defaults().get("system_prompt") or ""
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
                        # 自由形状载荷：让模型能传参数（如 ui 的 component/target/props）
                        "payload": {
                            "type": "object",
                            "description": "该调用的参数对象（键值对，按能力需要填）",
                        },
                    }},
                },
            }
            for cap in ctx.capabilities
        ]

        # 2) 问模型：按 slot 折叠各节点消息（不认具体名字）。
        #    input_fragments 是 dict，保持插入顺序 = _prepare_input 的注入顺序，
        #    天然保证"注入顺序 = 消费顺序"，无需维护第二份列表。
        #    system 槽（prompt/budget 等治理指令）在前，user 槽（context/memory/_tool）在后。
        messages: List[LLMMsg] = []
        system_msgs: List[LLMMsg] = []
        for target, msgs in ctx.input_fragments.items():
            if ctx.input_slots.get(target) == "system":
                system_msgs.extend(msgs)
            else:
                messages.extend(msgs)
        messages = system_msgs + messages
        # 兜底：无任何输入节点时用最简默认
        if not messages:
            messages = [
                LLMMsg("system", self._system),
                LLMMsg("user", self._prompt(ctx)),
            ]
        result = await self._backend.chat(
            model=self._model,
            messages=messages,
            options={"tools": tools, "temperature": self._temperature},
        )

        # 3) 翻译成答题卡：模型选中一个工具调用 → 就是"下一步点名谁"
        if result.tool_calls:
            call = result.tool_calls[0]
            args = dict(call.arguments)
            # 若模型把参数包进 payload 子对象，平铺进顶层（与其它键共存）
            if isinstance(args.get("payload"), dict):
                args.update(args.pop("payload"))
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
