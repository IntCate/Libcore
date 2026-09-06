"""im 平台适配器：企业微信（WeCom）。

接入逻辑借鉴 hermes-agent 的 ``plugins/platforms/wecom/adapter.py``。

**注意**：企业微信 Smart Robot 的发送依赖 WebSocket 长连接（``aibot_send_msg``
命令 + 关联响应），属于"长驻网关"模式，与 agentOS"能力节点 = 原子动作"的
定位不匹配。因此本适配器**暂不实现 send**，降级为 not_implemented（诚实披露）。

配置：``WECOM_BOT_ID`` / ``WECOM_SECRET``（Smart Robot WebSocket 模式）。

契约：``adapter(op, payload) -> dict``。当前仅支持 ``status``。
"""
from __future__ import annotations

from typing import Any, Dict


class WeComAdapter:
    """企业微信适配器：当前仅支持 status（send 依赖 WebSocket 长连接，未实现）。"""

    def __init__(self, bot_id: str, secret: str) -> None:
        self._bot_id = bot_id
        self._secret = secret

    def __call__(self, op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if op == "status":
            return {"connected": bool(self._bot_id and self._secret), "platform": "wecom"}
        raise NotImplementedError(
            f"wecom 不支持 op: {op}（企业微信发送依赖 WebSocket 长连接，agentOS 原子动作契约下未实现）"
        )
