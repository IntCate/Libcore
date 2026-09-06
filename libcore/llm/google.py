"""google-generative-ai API Family 适配器（极简 ChatBackend 契约）。

覆盖供应商：
    Google Generative AI（Gemini Pro/Flash 2.5/3.0）

设计要点（SD-1：httpx 直连）：
    1. 直接 httpx 调用 /v1beta/models/{model}:generateContent。
    2. 消息格式翻译：LLMMsg → Gemini contents（role 仅 user/model）。
    3. system instruction → 顶层 systemInstruction 字段。
    4. tool 调用：functionCall。
    5. API key 在 URL query 参数（不在 header）。

Gemini API 差异：
    - role 仅 "user" / "model"（assistant → model）
    - system 消息提取到顶层 systemInstruction
    - content 是 parts 数组：[{text: "..."}, {functionCall: {name, args}}]
    - generateContent 返回 candidates[0].content.parts

不变量守护：
    仅 import httpx，无 provider SDK，零 langchain_ 引用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from .http_client import build_keepalive_http_client
from .retry import RetryExhausted, RetryFatal, classify_httpx_error, retry_context
from .spi import ChatResult, LLMMsg, ToolCall


class GoogleGenerativeAIBackend:
    """google-generative-ai API Family 适配器（实现 ChatBackend）。

    用法：
        backend = GoogleGenerativeAIBackend(api_key="AIza...")
        result = await backend.chat(model="gemini-2.5-pro", messages=[...], options={...})
    """

    def __init__(
        self,
        *,
        base_url: str = "https://generativelanguage.googleapis.com",
        api_key: Optional[str] = None,
        timeout: float = 180.0,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._client = build_keepalive_http_client(
            base_url=base_url, headers={"Content-Type": "application/json"},
        )
        self._timeout = timeout

    async def chat(
        self,
        *,
        model: str,
        messages: List[LLMMsg],
        options: Dict[str, Any],
    ) -> ChatResult:
        payload = self._build_payload(model, messages, options)
        url = f"/v1beta/models/{model}:generateContent?key={self._api_key or ''}"
        max_retries = int(options.get("max_retries", 2))

        try:
            async with retry_context(
                max_attempts=max_retries,
                error_classifier=lambda e: classify_httpx_error(e, "google-generative-ai"),
            ) as ctx:
                async for attempt in ctx:
                    try:
                        resp = await self._client.post(
                            url, json=payload, timeout=self._timeout,
                        )
                        resp.raise_for_status()
                        await attempt.success()
                        return self._parse_chat_response(resp.json(), model)
                    except Exception as exc:
                        await attempt.fail(exc)
        except RetryFatal as e:
            return ChatResult(content=f"[Fatal({e.error_info.category.value})] {e.error_info.error_reason}")
        except RetryExhausted as e:
            return ChatResult(content=f"[RetryExhausted({e.total_attempts}x)] {e.last_error_info.error_reason}")
        return ChatResult(content="[retry_context exited without result]")

    # ------------------------------------------------------------
    # Payload 构造
    # ------------------------------------------------------------

    def _build_payload(
        self,
        model: str,
        messages: List[LLMMsg],
        options: Dict[str, Any],
    ) -> Dict[str, Any]:
        system_parts: List[Dict[str, Any]] = []
        contents: List[Dict[str, Any]] = []

        for msg in messages:
            if msg.role in ("system", "developer"):
                system_parts.append({"text": msg.content})
            else:
                role = "model" if msg.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": msg.content}]})

        payload: Dict[str, Any] = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        gen_config: Dict[str, Any] = {}
        if options.get("temperature") is not None:
            gen_config["temperature"] = options["temperature"]
        if options.get("max_tokens") is not None:
            gen_config["maxOutputTokens"] = options["max_tokens"]
        if options.get("top_p") is not None:
            gen_config["topP"] = options["top_p"]
        if options.get("stop"):
            gen_config["stopSequences"] = options["stop"]
        if gen_config:
            payload["generationConfig"] = gen_config

        if options.get("tools"):
            payload["tools"] = [{
                "functionDeclarations": [self._convert_tool_schema(t) for t in options["tools"]],
            }]
        return payload

    def _convert_tool_schema(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """JSON Schema → Gemini functionDeclarations 格式。"""
        if tool.get("type") == "function" and "function" in tool:
            fn = tool["function"]
            return {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            }
        return {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {}),
        }

    # ------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------

    def _parse_chat_response(self, data: Dict[str, Any], model: str) -> ChatResult:
        text = ""
        calls: List[ToolCall] = []

        candidates = data.get("candidates", [])
        if candidates:
            content = candidates[0].get("content", {})
            for part in content.get("parts", []) or []:
                if "text" in part:
                    text += part["text"]
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    calls.append(ToolCall(
                        name=fc.get("name", ""),
                        arguments=fc.get("args", {}) or {},
                    ))

        return ChatResult(content=text, tool_calls=calls)
