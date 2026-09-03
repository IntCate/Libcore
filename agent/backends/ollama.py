"""参考适配器：直接调用 ollama HTTP API 的 ChatBackend（官方库最小用法）。

用法：
    backend = OllamaBackend()
    reason  = LlmReasonProvider(backend, model="qwen3:0.6b")

零额外依赖（用 httpx）。若改 langchain/别的库，换一个 backend 即可，框架零改动。
"""
from __future__ import annotations

from typing import Any, Dict, List

import httpx

from ..lm_reason import ChatResult, LLMMsg, ToolCall


class OllamaBackend:
    """用 ollama 的 /api/chat 原生接口实现 ChatBackend。"""

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=180.0)

    async def chat(
        self,
        *,
        model: str,
        messages: List[LLMMsg],
        options: Dict[str, Any],
    ) -> ChatResult:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": options.get("temperature", 0.2)},
        }
        if options.get("tools"):
            payload["tools"] = options["tools"]

        resp = (await self._client.post("/api/chat", json=payload)).json()
        msg = resp.get("message", {}) or {}

        text = msg.get("content", "") or ""
        calls: List[ToolCall] = []
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or {}
            if isinstance(args, str):  # 有些返回把 arguments 序列化成字符串
                try:
                    import json as _json

                    args = _json.loads(args)
                except Exception:
                    args = {}
            if name:
                calls.append(ToolCall(name=name, arguments=args))

        return ChatResult(content=text, tool_calls=calls)