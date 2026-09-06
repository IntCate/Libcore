"""im 平台适配器：腾讯元宝（Yuanbao）。

接入逻辑借鉴 hermes-agent 的 ``gateway/platforms/yuanbao.py``。

**注意**：元宝的发送依赖 WebSocket 网关（AUTH_BIND 认证 + T06 发送 + 心跳重连），
属于"长驻网关"模式，与 agentOS"能力节点 = 原子动作"的定位不匹配。因此本适配器
**暂不实现 send**，降级为 not_implemented（诚实披露）。

配置：``YUANBAO_APP_ID`` / ``YUANBAO_APP_SECRET`` / ``YUANBAO_BOT_ID``。

契约：``adapter(op, payload) -> dict``。当前仅支持 ``status``。
"""
from __future__ import annotations

from typing import Any, Dict


class YuanbaoAdapter:
    """元宝适配器：当前仅支持 status（send 依赖 WebSocket 网关，未实现）。"""

    def __init__(self, app_id: str, app_secret: str, bot_id: str = "") -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._bot_id = bot_id

    def __call__(self, op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if op == "status":
            return {"connected": bool(self._app_id and self._app_secret), "platform": "yuanbao"}
        raise NotImplementedError(
            f"yuanbao 不支持 op: {op}（元宝发送依赖 WebSocket 网关，agentOS 原子动作契约下未实现）"
        )
