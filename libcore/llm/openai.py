"""openai-completions API Family 适配器（极简 ChatBackend 契约）。

覆盖供应商（共享 openai-completions 协议族）：
    OpenAI / DeepSeek / Kimi / MiniMax / ZAI / 阿里云百炼 / Volc / SiliconFlow / 本地兼容端点

设计要点（SD-1 决策：httpx 直连）：
    1. 不使用 openai SDK，直接 httpx 调用 /v1/chat/completions。
    2. 复用 http_client.py 的 keepalive 连接池。
    3. 消息格式翻译：LLMMsg → OpenAI request payload（极简契约下消息均为纯文本）。
    4. 响应归一化：OpenAI response → ChatResult（text + tool_calls）。
    5. 重试由 retry.py 统一控制。

不变量守护：
    仅 import httpx，无 provider SDK，零 langchain_ 引用。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from .http_client import build_keepalive_http_client
from .retry import RetryExhausted, RetryFatal, classify_httpx_error, retry_context
from .spi import ChatResult, LLMMsg, ToolCall


class OpenAICompletionsBackend:
    """openai-completions API Family 适配器（实现 ChatBackend）。

    用法：
        backend = OpenAICompletionsBackend(api_key="sk-xxx")
        result = await backend.chat(model="gpt-4o", messages=[...], options={...})
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com/v1",
        api_key: Optional[str] = None,
        timeout: float = 180.0,
    ) -> None:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = build_keepalive_http_client(base_url=base_url, headers=headers)
        self._timeout = timeout

    async def chat(
        self,
        *,
        model: str,
        messages: List[LLMMsg],
        options: Dict[str, Any],
    ) -> ChatResult:
        payload = self._build_payload(model, messages, options)
        max_retries = int(options.get("max_retries", 2))

        try:
            async with retry_context(
                max_attempts=max_retries,
                error_classifier=lambda e: classify_httpx_error(e, "openai-completions"),
            ) as ctx:
                async for attempt in ctx:
                    try:
                        resp = await self._client.post(
                            "/chat/completions", json=payload, timeout=self._timeout,
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
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }
        if options.get("temperature") is not None:
            payload["temperature"] = options["temperature"]
        if options.get("max_tokens") is not None:
            payload["max_tokens"] = options["max_tokens"]
        if options.get("top_p") is not None:
            payload["top_p"] = options["top_p"]
        if options.get("stop"):
            payload["stop"] = options["stop"]
        if options.get("response_format"):
            payload["response_format"] = options["response_format"]
        if options.get("tools"):
            payload["tools"] = options["tools"]
        return payload

    # ------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------

    def _parse_chat_response(self, data: Dict[str, Any], model: str) -> ChatResult:
        choices = data.get("choices", [])
        text = ""
        calls: List[ToolCall] = []

        if choices:
            message = choices[0].get("message", {})
            text = message.get("content") or ""
            for tc in message.get("tool_calls", []) or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                if fn.get("name"):
                    calls.append(ToolCall(name=fn["name"], arguments=args))

        return ChatResult(content=text, tool_calls=calls)
