"""im 平台适配器：QQ 机器人（QQ Bot）。

接入逻辑借鉴 hermes-agent 的 ``gateway/platforms/qqbot/adapter.py``
（只借鉴 REST API 的 token 获取与按 chat 类型路由发送，不引入其 WebSocket 入站）。

依赖：``aiohttp``。
配置：``QQ_APP_ID`` / ``QQ_CLIENT_SECRET``（必填）。

契约：``adapter(op, payload) -> dict``。当前支持 ``send``（文本）与 ``status``。
chat_id 约定：``c2c:<openid>`` / ``group:<group_openid>`` / ``guild:<channel_id>``。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, Optional

try:
    import aiohttp
except ImportError:  # 未安装依赖时惰性降级
    aiohttp = None  # type: ignore[assignment]

API_BASE = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
MSG_TYPE_TEXT = 0


class QQBotAdapter:
    """QQ 机器人适配器：用 REST API 发文本消息。"""

    def __init__(self, app_id: str, client_secret: str) -> None:
        if aiohttp is None:
            raise RuntimeError("aiohttp 未安装，无法使用 QQ 适配器")
        self._app_id = app_id
        self._client_secret = client_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def __call__(self, op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if op == "send":
            return self._send(payload)
        if op == "status":
            return {"connected": bool(self._app_id and self._client_secret), "platform": "qqbot"}
        raise NotImplementedError(f"qqbot 不支持 op: {op}")

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        chat_id = payload.get("chat_id")
        text = payload.get("text")
        if not chat_id or not text:
            raise ValueError("qqbot send 需要 chat_id 与 text")
        result = asyncio.run(self._send_text(chat_id, text))
        return {"message_id": result}

    async def _send_text(self, chat_id: str, text: str) -> str:
        token = await self._ensure_token()
        if chat_id.startswith("c2c:"):
            openid = chat_id[4:]
            path = f"/v2/users/{openid}/messages"
        elif chat_id.startswith("group:"):
            group_openid = chat_id[6:]
            path = f"/v2/groups/{group_openid}/messages"
        elif chat_id.startswith("guild:"):
            channel_id = chat_id[6:]
            path = f"/channels/{channel_id}/messages"
        else:
            raise ValueError(f"qqbot 未知 chat_id 类型: {chat_id}")

        body = {"content": text, "msg_type": MSG_TYPE_TEXT, "msg_seq": int(time.time() * 1000)}
        data = await self._api_request("POST", path, body, token)
        return str(data.get("id", uuid.uuid4().hex[:12]))

    async def _ensure_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        async with aiohttp.ClientSession() as session:
            async with session.post(
                TOKEN_URL,
                json={"appId": self._app_id, "clientSecret": self._client_secret},
                timeout=15.0,
            ) as response:
                if not response.ok:
                    raise RuntimeError(f"QQ token HTTP {response.status}")
                data = await response.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"QQ token response missing access_token: {data}")
        self._access_token = token
        self._token_expires_at = time.time() + int(data.get("expires_in", 7200))
        return token

    async def _api_request(self, method: str, path: str, body: Dict[str, Any], token: str) -> Dict[str, Any]:
        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, f"{API_BASE}{path}", headers=headers, json=body, timeout=15.0,
            ) as response:
                data = await response.json()
                if response.status >= 400:
                    raise RuntimeError(
                        f"QQ Bot API error [{response.status}] {path}: {data.get('message', data)}"
                    )
                return data
