"""飞书入站监听器：用 lark_oapi WebSocket 长连接收消息，转成 InboundMessage。

接入逻辑借鉴 hermes-agent 的 ``plugins/platforms/feishu/adapter.py``：
- 用 ``lark_oapi.ws.Client`` 建立 WebSocket 长连接（带 ``channel`` UA 标签，
  否则飞书服务器不会推送群 @mention 事件）；
- 注册 ``im.message.receive_v1`` 事件回调，解析出 chat_id / user_id / text；
- 收到消息后经注入的 ``on_message`` 回调交给上层（ImChannel.ingest）。

依赖：``lark-oapi``。未安装时惰性降级，装配方未接线该平台。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        P2ImMessageReceiveV1,
        P2ImMessageReceiveV1Data,
    )
except ImportError:  # 未安装依赖时惰性降级
    lark = None  # type: ignore[assignment]
    P2ImMessageReceiveV1 = None  # type: ignore[assignment]
    P2ImMessageReceiveV1Data = None  # type: ignore[assignment]

_logger = logging.getLogger("libcore.channels.feishu_listener")

# 收到消息后的回调：listener(chat_id, user_id, text) -> None
OnMessage = Callable[[str, str, str], None]


class FeishuListener:
    """飞书入站监听器：WebSocket 长连接收消息，转成 (chat_id, user_id, text)。"""

    def __init__(self, app_id: str, app_secret: str, on_message: OnMessage,
                 domain: str = "feishu") -> None:
        if lark is None:
            raise RuntimeError("lark-oapi 未安装，无法使用飞书监听器")
        self._app_id = app_id
        self._app_secret = app_secret
        self._domain = domain
        self._on_message = on_message
        self._ws: Any = None

    def start(self) -> None:
        """启动 WebSocket 长连接（阻塞，需在后台线程/任务运行）。"""
        handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._on_message_event) \
            .build()
        self._ws = lark.ws.Client(
            self._app_id, self._app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.ERROR,
            extra_ua_tags=["channel"],  # 关键：启用群 @mention 推送
        )
        _logger.info("feishu listener starting (websocket)")
        self._ws.start()

    def stop(self) -> None:
        """停止长连接。"""
        if self._ws is not None:
            try:
                self._ws.stop()
            except Exception:  # noqa: BLE001 - 停止失败不致命
                pass
            self._ws = None

    def _on_message_event(self, data: Any) -> None:
        """lark_oapi 事件回调（后台线程调用）。解析出 chat_id/user_id/text。"""
        try:
            event = getattr(data, "event", None)
            message = getattr(event, "message", None)
            sender = getattr(event, "sender", None)
            if message is None:
                return
            chat_id = getattr(message, "chat_id", "") or ""
            # 发送者：优先 user_id（tenant 级），回退 open_id（app 级）
            sender_id = getattr(sender, "sender_id", None)
            user_id = (getattr(sender_id, "user_id", None)
                       or getattr(sender_id, "open_id", None) or "")
            # 正文：message.content 是 JSON 字符串，text 类型取 payload["text"]
            text = self._extract_text(message)
            if not text:
                return
            self._on_message(chat_id, user_id, text)
        except Exception as e:  # noqa: BLE001 - 单条消息解析失败不拖垮监听
            _logger.error("feishu message parse error: %s", e)

    @staticmethod
    def _extract_text(message: Any) -> str:
        """从 message 提取文本正文。text 类型取 content JSON 的 text 字段。"""
        content = getattr(message, "content", None)
        if not content:
            return ""
        try:
            payload = json.loads(content) if isinstance(content, str) else content
        except (ValueError, TypeError):
            return ""
        if isinstance(payload, dict):
            return str(payload.get("text", "")).strip()
        return ""
