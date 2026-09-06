"""anthropic-messages API Family 适配器（极简 ChatBackend 契约）。

覆盖供应商：
    Anthropic（Claude Opus/Sonnet/Haiku 4/5）+ 兼容端点（Vertex Claude 等）

设计要点（SD-1：httpx 直连）：
    1. 直接 httpx 调用 /v1/messages（不依赖 anthropic SDK）。
    2. 消息格式翻译：LLMMsg → Anthropic messages payload（system 单独提取）。
    3. 响应归一化：Anthropic response → ChatResult（text + tool_calls）。
    4. 重试由 retry.py 统一控制。

Anthropic API 与 OpenAI 差异：
    - system 是顶层字段（不在 messages 数组里）
    - content 是数组结构：[{type: "text", text: "..."}, ...]
    - tool_use: {id, name, input}（不是 arguments 字符串）

不变量守护：
    仅 import httpx，无 provider SDK，零 langchain_ 引用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from .http_client import build_keepalive_http_client
from .retry import RetryExhausted, RetryFatal, classify_httpx_error, retry_context
from .spi import ChatResult, LLMMsg, ToolCall


class AnthropicMessagesBackend:
    """anthropic-messages API Family 适配器（实现 ChatBackend）。

    用法：
        backend = AnthropicMessagesBackend(api_key="sk-ant-xxx")
        result = await backend.chat(model="claude-sonnet-4-5", messages=[...], options={...})
    """

    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(
        self,
        *,
        base_url: str = "https://api.anthropic.com",
        api_key: Optional[str] = None,
        timeout: float = 180.0,
    ) -> None:
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "anthropic-version": self.ANTHROPIC_VERSION,
        }
        if api_key:
            headers["x-api-key"] = api_key
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
                error_classifier=lambda e: classify_httpx_error(e, "anthropic-messages"),
            ) as ctx:
                async for attempt in ctx:
                    try:
                        resp = await self._client.post(
                            "/v1/messages", json=payload, timeout=self._timeout,
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
        system_parts: List[str] = []
        anthropic_messages: List[Dict[str, Any]] = []

        for msg in messages:
            if msg.role in ("system", "developer"):
                system_parts.append(msg.content)
            else:
                anthropic_messages.append({
                    "role": msg.role,
                    "content": [{"type": "text", "text": msg.content}],
                })

        payload: Dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": options.get("max_tokens") or 4096,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        if options.get("temperature") is not None:
            payload["temperature"] = options["temperature"]
        if options.get("top_p") is not None:
            payload["top_p"] = options["top_p"]
        if options.get("stop"):
            payload["stop_sequences"] = options["stop"]
        if options.get("tools"):
            payload["tools"] = [self._convert_tool_schema(t) for t in options["tools"]]
        return payload

    def _convert_tool_schema(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """JSON Schema → Anthropic tools 字段格式。"""
        if tool.get("type") == "function" and "function" in tool:
            fn = tool["function"]
            return {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {}),
            }
        return {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "input_schema": tool.get("parameters", {}),
        }

    # ------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------

    def _parse_chat_response(self, data: Dict[str, Any], model: str) -> ChatResult:
        text = ""
        calls: List[ToolCall] = []

        for block in data.get("content", []) or []:
            block_type = block.get("type")
            if block_type == "text":
                text += block.get("text", "")
            elif block_type == "tool_use":
                calls.append(ToolCall(
                    name=block.get("name", ""),
                    arguments=block.get("input", {}) or {},
                ))

        return ChatResult(content=text, tool_calls=calls)
