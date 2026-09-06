"""im 平台适配器：微信（Weixin，iLink 协议）。

接入逻辑借鉴 hermes-agent 的 ``gateway/platforms/weixin.py``
（只借鉴 iLink ``sendmessage`` 的请求构造与头信息，不引入其网关级抽象）。

依赖：``aiohttp``。
配置：``WEIXIN_ACCOUNT_ID`` / ``WEIXIN_TOKEN``（必填），``WEIXIN_BASE_URL``
（默认 ``https://ilinkai.weixin.qq.com``）。

契约：``adapter(op, payload) -> dict``。当前支持 ``send``（文本）与 ``status``。
"""
from __future__ import annotations

import asyncio
import base64
import json
import secrets
import struct
import uuid
from typing import Any, Dict, Optional

try:
    import aiohttp
except ImportError:  # 未安装依赖时惰性降级
    aiohttp = None  # type: ignore[assignment]

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "2.2.0"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2
ITEM_TEXT = 1
API_TIMEOUT_MS = 15_000


def _random_wechat_uin() -> str:
    value = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _headers(token: str, body: str) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class WeixinAdapter:
    """微信适配器：用 iLink 协议发文本消息。"""

    def __init__(self, account_id: str, token: str, base_url: str = ILINK_BASE_URL) -> None:
        if aiohttp is None:
            raise RuntimeError("aiohttp 未安装，无法使用微信适配器")
        self._account_id = account_id
        self._token = token
        self._base_url = base_url.rstrip("/")

    def __call__(self, op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if op == "send":
            return self._send(payload)
        if op == "status":
            return {"connected": bool(self._token), "platform": "weixin"}
        raise NotImplementedError(f"weixin 不支持 op: {op}")

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        chat_id = payload.get("chat_id")
        text = payload.get("text")
        if not chat_id or not text:
            raise ValueError("weixin send 需要 chat_id 与 text")
        message: Dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": chat_id,
            "client_id": f"libcore-weixin-{uuid.uuid4().hex}",
            "message_type": MSG_TYPE_BOT,
            "message_state": MSG_STATE_FINISH,
            "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
        }
        body = json.dumps(
            {**{"msg": message}, "base_info": {"channel_version": CHANNEL_VERSION}},
            ensure_ascii=False, separators=(",", ":"),
        )
        result = asyncio.run(self._api_post(EP_SEND_MESSAGE, body))
        if result.get("ret") not in {0, None} or result.get("errcode") not in {0, None}:
            raise RuntimeError(f"iLink sendmessage error: {result}")
        return {"message_id": message["client_id"]}

    async def _api_post(self, endpoint: str, body: str) -> Dict[str, Any]:
        url = f"{self._base_url}/{endpoint}"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=body, headers=_headers(self._token, body)) as response:
                raw = await response.text()
                if not response.ok:
                    raise RuntimeError(f"iLink POST {endpoint} HTTP {response.status}: {raw[:200]}")
                return json.loads(raw)
