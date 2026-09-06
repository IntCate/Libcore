"""内置能力插件：ui —— agent 决定渲染什么，产出渲染指令（agentOS 系统能力）。

语义：agent 具备**渲染能力**——它决定渲染什么界面、用什么组件、怎么布局、绑什么数据，
产出**渲染指令**（数据契约）。渲染执行在节点内部（或交给渲染层），agent 不碰像素。

产出约定：``data["render"]`` 为渲染指令（组件/布局/数据契约）。
无渲染后端 → 降级为 not_implemented（诚实披露，绝不编造渲染结果）。

装配（零耦合，可拆卸）：
- 默认从 payload 读取 ``payload["type"]``（组件类型）/ ``payload["layout"]`` / ``payload["data"]``，
  经注入的 ``renderer`` 产出渲染指令；
- ``register(bus, renderer=)`` 可注入真实渲染后端（签名 ``renderer(spec) -> dict``）；
- ``register(bus, channels=)`` 可注入通道注册表（ChannelRegistry）：当 payload 带
  ``channel_id`` 时，render 优先经对应 Channel.reply 送回原会话（统一出站链路），
  否则回退到 renderer（兼容现有手动渲染）；
- 本节点不 dispatch 任何其他节点。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from libcore.kernel.bus import Dispatch, CapabilityResult

DESCRIPTION = (
    "系统能力节点：agent 调度我决定渲染什么。find 列出前端当前可用组件清单（components），"
    "render 决定组件/布局/数据，产出渲染指令（data['render']）。渲染执行在节点内部，"
    "agent 不碰像素。无后端降级为 not_implemented。"
)

# renderer 类型：接收渲染规格，返回渲染指令 dict
Renderer = Callable[[Dict[str, Any]], Dict[str, Any]]


def _default_renderer(spec: Dict[str, Any]) -> Dict[str, Any]:
    """默认 renderer：占位，诚实披露未接线（绝不编造渲染结果）。"""
    raise NotImplementedError("ui 渲染后端未接线，无法渲染")


def register(bus, renderer: Optional[Renderer] = None, manifest_store=None,
             channels=None) -> None:
    """注册 ui 能力节点。

    - ``renderer``：可注入真实渲染后端（签名 ``renderer(spec) -> dict``）；缺省占位降级。
    - ``manifest_store``：可注入前端组件清单存储（``snapshot() -> {"components": [...]}``），
      使 ``ui find`` 能向 agent 披露"前端现在有哪些组件可用"，避免 agent 盲猜组件名。
    - ``channels``：可注入通道注册表（ChannelRegistry）。当 payload 带 ``channel_id``
      且注册表中有对应通道时，render 优先经 ``Channel.reply`` 送回原会话（统一出站链路）；
      否则回退到 renderer（兼容现有手动渲染）。
    """
    render = renderer or _default_renderer

    def handle(d: Dispatch) -> CapabilityResult:
        op = d.op or "render"
        if op == "find":
            # 渐进披露：返回前端当前可用组件清单（供 agent 决策选组件，省 token）
            if manifest_store is None:
                return CapabilityResult(ok=False, error="ui 组件清单未接线")
            components = list((manifest_store.snapshot() or {}).get("components", []))
            return CapabilityResult(ok=True, data={"components": components, "count": len(components)})
        # 统一出站：payload 带 channel_id 且注册表有对应通道 → 经 Channel.reply 送回原会话
        channel_id = d.payload.get("channel_id")
        if channel_id and channels is not None:
            channel = channels.get(channel_id)
            if channel is not None:
                from libcore.channels.message import OutboundMessage
                channel.reply(OutboundMessage(
                    channel_id=channel_id, kind=channel.kind, platform="ui",
                    text=d.payload.get("text", ""),
                ))
                return CapabilityResult(ok=True, data={"status": "sent", "channel_id": channel_id})
        # 透传完整渲染语义（组件/容器/props/事件/code 源码），renderer 据此构建渲染消息。
        # target 为容器地址（/root/** 或 /ext/**）；type 缺省按 op 归一。
        spec = {
            "type": d.payload.get("type") or (op if op in ("render", "code", "data", "close") else "render"),
            "target": d.payload.get("target"),
            "component": d.payload.get("component"),
            "props": d.payload.get("props"),
            "events": d.payload.get("events"),
            "layout": d.payload.get("layout"),
            "data": d.payload.get("data"),
            "title": d.payload.get("title"),
            "lang": d.payload.get("lang"),
            "source": d.payload.get("source"),
        }
        try:
            render_cmd = render(spec)
        except NotImplementedError:
            return CapabilityResult(
                ok=False, data={"status": "not_implemented", "op": op, "type": spec["type"]},
                error="ui 渲染后端未接线，暂无法渲染",
            )
        except Exception as e:
            return CapabilityResult(ok=False, data={}, error=f"ui 渲染失败: {e}")
        return CapabilityResult(ok=True, data={"render": render_cmd})

    bus.on("ui", handle, meta={
        "description": DESCRIPTION,
        "ops": ["find", "render"],
    })
