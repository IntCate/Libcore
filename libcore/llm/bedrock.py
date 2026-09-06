"""bedrock-converse-stream API Family 适配器（极简 ChatBackend 契约）。

覆盖供应商：
    AWS Bedrock（Claude 4.5/3.7/3.5 等基础模型，通过 Converse API）

设计要点（TD-7：可选依赖）：
    1. boto3 是**可选**依赖——未安装时本模块不定义 adapter 类。
    2. 直连 boto3 bedrock-runtime 客户端（不依赖 provider SDK 包装层）。
    3. 消息翻译：LLMMsg → Converse messages（system 提取到顶层）。
    4. 响应归一化：Converse response → ChatResult（text + tool_calls）。

不变量守护：
    仅 import boto3（可选），零 langchain_ 引用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .spi import ChatResult, LLMMsg, ToolCall

try:  # pragma: no cover - 环境依赖分支
    import boto3  # type: ignore
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore


if boto3 is not None:

    class BedrockConverseBackend:
        """bedrock-converse-stream API Family 适配器（实现 ChatBackend）。

        用法：
            backend = BedrockConverseBackend(
                aws_access_key_id="AKIA...",
                aws_secret_access_key="...",
                aws_region="us-east-1",
            )
            result = await backend.chat(model="anthropic.claude-sonnet-4-5-v2", messages=[...], options={...})

        测试注入：设置 `backend._client = fake_client` 可绕过 boto3 直连。
        """

        def __init__(
            self,
            *,
            aws_access_key_id: Optional[str] = None,
            aws_secret_access_key: Optional[str] = None,
            aws_region: Optional[str] = None,
        ) -> None:
            self._aws_access_key_id = aws_access_key_id
            self._aws_secret_access_key = aws_secret_access_key
            self._aws_region = aws_region
            self._client: Any = None  # 测试注入点（绕过 boto3）

        def _get_client(self) -> Any:
            if self._client is not None:
                return self._client
            if boto3 is None:  # pragma: no cover - 已由模块级守卫排除
                raise RuntimeError("boto3 未安装：无法使用 AWS Bedrock Converse（可选依赖）")
            kwargs: Dict[str, Any] = {}
            if self._aws_region:
                kwargs["region_name"] = self._aws_region
            if self._aws_access_key_id:
                kwargs["aws_access_key_id"] = self._aws_access_key_id
            if self._aws_secret_access_key:
                kwargs["aws_secret_access_key"] = self._aws_secret_access_key
            return boto3.client("bedrock-runtime", **kwargs)

        async def chat(
            self,
            *,
            model: str,
            messages: List[LLMMsg],
            options: Dict[str, Any],
        ) -> ChatResult:
            request = self._build_request(model, messages, options)
            client = self._get_client()
            try:
                resp = client.converse(**request)
            except Exception as exc:  # noqa: BLE001 — 统一映射为 ERROR
                return ChatResult(content=f"[Bedrock error] {str(exc)[:500]}")
            return self._parse_converse_response(model, resp)

        # ------------------------------------------------------------
        # Payload 构造
        # ------------------------------------------------------------

        def _build_request(
            self,
            model: str,
            messages: List[LLMMsg],
            options: Dict[str, Any],
        ) -> Dict[str, Any]:
            system_parts: List[Dict[str, Any]] = []
            converse_messages: List[Dict[str, Any]] = []

            for msg in messages:
                if msg.role in ("system", "developer"):
                    system_parts.append({"text": msg.content})
                else:
                    role = "assistant" if msg.role == "assistant" else "user"
                    converse_messages.append({"role": role, "content": [{"text": msg.content}]})

            request: Dict[str, Any] = {"modelId": model, "messages": converse_messages}
            if system_parts:
                request["system"] = system_parts

            inference: Dict[str, Any] = {}
            if options.get("temperature") is not None:
                inference["temperature"] = options["temperature"]
            if options.get("max_tokens") is not None:
                inference["maxTokens"] = options["max_tokens"]
            if options.get("top_p") is not None:
                inference["topP"] = options["top_p"]
            if options.get("stop"):
                inference["stopSequences"] = options["stop"]
            if inference:
                request["inferenceConfig"] = inference

            if options.get("tools"):
                request["toolConfig"] = {
                    "tools": [self._convert_tool_schema(t) for t in options["tools"]],
                }
            return request

        def _convert_tool_schema(self, tool: Dict[str, Any]) -> Dict[str, Any]:
            """JSON Schema → Converse toolSpec 格式。"""
            if tool.get("type") == "function" and "function" in tool:
                fn = tool["function"]
                name = fn.get("name", "")
                description = fn.get("description", "")
                parameters = fn.get("parameters", {})
            else:
                name = tool.get("name", "")
                description = tool.get("description", "")
                parameters = tool.get("parameters", tool)
            return {
                "toolSpec": {
                    "name": name,
                    "description": description or "",
                    "inputSchema": {"json": parameters},
                },
            }

        # ------------------------------------------------------------
        # 响应解析
        # ------------------------------------------------------------

        def _parse_converse_response(self, model: str, resp: Dict[str, Any]) -> ChatResult:
            text = ""
            calls: List[ToolCall] = []
            message = resp.get("output", {}).get("message", {}) or {}
            for block in message.get("content", []) or []:
                if "text" in block:
                    text += block["text"]
                elif "toolUse" in block:
                    tool_use = block.get("toolUse") or {}
                    calls.append(ToolCall(
                        name=tool_use.get("name", ""),
                        arguments=tool_use.get("input", {}) or {},
                    ))
            return ChatResult(content=text, tool_calls=calls)
