"""im 平台适配器：飞书（Feishu/Lark）。

接入逻辑借鉴 hermes-agent 的 ``plugins/platforms/feishu/adapter.py``
（只借鉴 ``lark_oapi`` 发消息的 SDK 调用方式，不引入其网关级抽象）。

依赖：``lark-oapi``（``import lark_oapi``）。
配置：``FEISHU_APP_ID`` / ``FEISHU_APP_SECRET``（必填），``FEISHU_DOMAIN``
（``feishu`` 国内 / ``lark`` 国际，默认 ``feishu``）。

契约：``adapter(op, payload) -> dict``。当前支持 ``send``（文本）与 ``status``。
"""
from __future__ import annotations

import json
from typing import Any, Dict

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
    )
except ImportError:  # 未安装依赖时惰性降级，装配方未接线该平台
    lark = None  # type: ignore[assignment]
    CreateMessageRequest = None  # type: ignore[assignment]
    CreateMessageRequestBody = None  # type: ignore[assignment]


class FeishuAdapter:
    """飞书适配器：用 ``lark_oapi`` 发文本消息。"""

    def __init__(self, app_id: str, app_secret: str, domain: str = "feishu") -> None:
        if lark is None:
            raise RuntimeError("lark-oapi 未安装，无法使用飞书适配器")
        self._app_id = app_id
        self._app_secret = app_secret
        self._domain = domain
        self._client = lark.Client.builder() \
            .app_id(app_id).app_secret(app_secret) \
            .log_level(lark.LogLevel.ERROR).build()

    def __call__(self, op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if op == "send":
            return self._send(payload)
        if op == "status":
            return {"connected": True, "platform": "feishu"}
        raise NotImplementedError(f"feishu 不支持 op: {op}")

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        chat_id = payload.get("chat_id")
        text = payload.get("text")
        if not chat_id or not text:
            raise ValueError("feishu send 需要 chat_id 与 text")
        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()) \
            .build()
        response = self._client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(f"feishu send failed: {response.code} {response.msg}")
        return {"message_id": response.data.message_id}
