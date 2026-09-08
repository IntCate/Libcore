"""内置能力插件：im —— 即时通讯入口（飞书/微信/Slack/Telegram，agentOS 系统能力）。

语义：agent 接管系统核心后，**IM 入口**是 agent 可调度的能力节点。
agent 决定收发什么消息、发到哪个平台/会话。

产出约定：``data["status"]`` 为发送结果；``data["message_id"]`` 为平台消息 id（可选）。
无平台适配器 → 降级为 not_implemented（诚实披露，绝不编造发送成功）。

装配（零耦合，可拆卸）：
- ``register(bus, registry=)`` 可注入平台适配器注册表（``IMRegistry``），
  每个平台适配器签名 ``adapter(op, payload) -> dict``（如飞书/微信/Slack/Telegram 的发送实现）；
- ``register(bus, channels=)`` 可注入通道注册表（ChannelRegistry）：当 payload 带
  ``channel_id`` 时，send 优先经对应 Channel.reply 送回原会话（统一出站链路），
  否则回退到平台适配器（兼容现有主动发送）；
- ``wire_feishu_listener(...)`` 可装配飞书入站监听（WebSocket 收消息 → ImChannel.ingest）；
- loader 调用 ``register(bus)`` 时 registry 为 None → 空注册表 → 所有平台 not_implemented；
- 本节点不 dispatch 任何其他节点。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from libcore.kernel.bus import Dispatch, CapabilityResult

DESCRIPTION = (
    "系统能力节点：agent 调度我收发 IM 消息（飞书/微信/Slack/Telegram 入口）。"
    "决定发什么消息、发到哪个平台/会话。list_platforms 列出已接线平台。"
    "无平台适配器降级为 not_implemented。"
)

# adapter 类型：接收 op + payload，返回平台结果 dict
IMAdapter = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class IMRegistry:
    """平台适配器注册表：platform -> adapter(op, payload) -> dict。

    装配方按平台名注册真实适配器（如 "feishu" / "weixin" / "slack" / "telegram"），
    ``im`` 节点按 ``payload["platform"]`` 路由到对应适配器。
    """

    def __init__(self) -> None:
        self._adapters: Dict[str, IMAdapter] = {}

    def register(self, platform: str, adapter: IMAdapter) -> None:
        """注册一个平台适配器。platform 如 "feishu" / "weixin" / "slack" / "telegram"。"""
        self._adapters[platform] = adapter

    def get(self, platform: str) -> Optional[IMAdapter]:
        return self._adapters.get(platform)

    def platforms(self) -> List[str]:
        return sorted(self._adapters)

    def dispatch(self, platform: str, op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """按平台路由到适配器；未注册平台抛 NotImplementedError（诚实披露）。"""
        adapter = self._adapters.get(platform)
        if adapter is None:
            raise NotImplementedError(f"im 平台适配器未接线: {platform}")
        return adapter(op, payload)


def register(bus, registry: Optional[IMRegistry] = None, channels=None) -> None:
    """注册 im 能力节点。

    - ``registry``：可注入平台适配器注册表；缺省空注册表降级。
    - ``channels``：可注入通道注册表（ChannelRegistry）。当 payload 带 ``channel_id``
      且注册表中有对应通道时，send 优先经 ``Channel.reply`` 送回原会话（统一出站链路）；
      否则回退到平台适配器（兼容现有主动发送）。
    """
    reg = registry or IMRegistry()

    def handle(d: Dispatch) -> CapabilityResult:
        op = d.op or "send"
        payload = d.payload or {}

        if op == "list_platforms":
            return CapabilityResult(ok=True, data={"platforms": reg.platforms()})

        # 统一出站：payload 带 channel_id 且注册表有对应通道 → 经 Channel.reply 送回原会话
        channel_id = payload.get("channel_id")
        if op == "send" and channel_id and channels is not None:
            channel = channels.get(channel_id)
            if channel is not None:
                from libcore.channels.message import OutboundMessage
                channel.reply(OutboundMessage(
                    channel_id=channel_id, kind=channel.kind,
                    platform=payload.get("platform", ""), text=payload.get("text", ""),
                ))
                return CapabilityResult(ok=True, data={"status": "sent", "channel_id": channel_id})

        platform = payload.get("platform")
        if not platform:
            return CapabilityResult(
                ok=False, data={"status": "missing_platform", "op": op},
                error="im 调用缺少 platform 字段",
            )
        try:
            result = reg.dispatch(platform, op, payload)
        except NotImplementedError:
            return CapabilityResult(
                ok=False, data={"status": "not_implemented", "op": op, "platform": platform},
                error=f"im 平台适配器未接线: {platform}",
            )
        except Exception as e:
            return CapabilityResult(ok=False, data={}, error=f"im {op} 失败: {e}")
        # 暴露内部适配器调用细节给 tracing/logging（不发布到总线，仅随结果上抛）：
        # 平台适配器是开放组件，不走总线，但门面把"内部路由到哪个平台、什么操作"
        # 塞进 result.data，使 tracing 能还原到具体平台这一层，而不只是 im 门面。
        return CapabilityResult(ok=True, data={
            "status": "sent" if op == "send" else "ok",
            **result,
            "_internal": {"platform": platform, "adapter_op": op},
        })

    bus.on("im", handle, meta={
        "description": DESCRIPTION,
        "ops": ["send", "receive", "list_platforms", "status"],
    })


def wire_feishu_listener(bus, channels, app_id: str, app_secret: str,
                         domain: str = "feishu", adapter=None) -> Any:
    """装配飞书入站监听：WebSocket 收消息 → ImChannel.ingest → 广播 channel.inbound。

    - 收到消息时按 ``im:feishu:{chat_id}`` 复用/创建 ImChannel（同一会话保持同一 channel_id）；
    - 出站 sender 复用飞书适配器（FeishuAdapter）发回原会话；
    - 返回 FeishuListener 实例，调用方负责在后台线程/任务 ``start()``。

    依赖 ``lark-oapi``；未安装时抛 RuntimeError（诚实披露，不静默降级）。

    ``adapter`` 可注入已构造的 FeishuAdapter（装配层便利，如经 ``build_adapter`` 统一取数）；
    缺省时按 ``app_id`` / ``app_secret`` 内部构造。
    """
    from libcore.channels.im_channel import ImChannel
    from libcore.channels.message import InboundMessage
    from libcore.channels.feishu_listener import FeishuListener
    from libcore.plugins.capabilities.platforms.feishu import FeishuAdapter

    if adapter is None:
        adapter = FeishuAdapter(app_id, app_secret, domain=domain)

    def _on_message(chat_id: str, user_id: str, text: str) -> None:
        channel_id = f"im:feishu:{chat_id}"
        channel = channels.get(channel_id)
        if channel is None:
            channel = ImChannel(bus, platform="feishu", chat_id=chat_id,
                                user_id=user_id, sender=adapter)
            channels.register(channel)
        channel.ingest(InboundMessage(
            channel_id=channel_id, kind="im", platform="feishu",
            user_id=user_id, text=text,
        ))

    return FeishuListener(app_id, app_secret, _on_message, domain=domain)
