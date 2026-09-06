"""渲染出口：承接 ui 能力节点产物 -> 封装渲染消息 -> push 到 WebSocket 下行。

作为 ``ui.register(bus, renderer=sink)`` 注入的 renderer，签名 ``renderer(spec) -> dict``：
- 输入 spec：agent 通过 ui 节点 payload 透传的语义参数（组件 / 容器 / props / source ...）；
- 产出：一条完整的渲染消息 ``{v, type, target, payload}``（**与前端 consumeRender 消费的 wire format 一致**）;
- 副作用：把该消息 ``push`` 到 WebSocket，前端据此渲染。

设计要点：renderer 只负责"决定渲染什么"，不关心"前端如何消费"；
渲染消息原样进 WS，前端 consumeRender 零改写即可渲染。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# 渲染消息协议版本，与前端 consumer.js 对齐
RENDER_PROTOCOL_VERSION = 1


class RenderSink:
    """渲染出口。构造后与 WebSocket 通道绑定，经 engine.push 下行。"""

    def __init__(self, engine, ws_channel: str) -> None:
        self._engine = engine
        self._ws_channel = ws_channel

    def __call__(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """构建渲染消息并 push 到浏览器。

        spec 字段（由 ui 能力节点从 payload 透传）：
            target      -> 容器 target，如 /root/chat（缺省按组件名生成 /root/<component>）
            type        -> render | code | data | close（缺省 render）
            component   -> 预置组件名（type=render）
            props       -> 组件 props（type=render）
            events      -> 事件绑定（type=render / code）
            data        -> 追加数据（type=data）
            lang/source -> code 分支源码（type=code）
            title       -> 可选标题
        """
        msg = self.build(spec)
        self._engine.push(self._ws_channel, msg)
        return msg

    # ---- 纯函数：spec -> 渲染消息（可单独测试）----

    def build(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        rtype = spec.get("type") or "render"
        component = spec.get("component")
        target = spec.get("target") or (f"/root/{component}" if component else "/root/default")

        base = {"v": RENDER_PROTOCOL_VERSION, "type": rtype, "target": target}
        payload: Dict[str, Any] = {}

        if rtype == "render":
            payload = {
                "component": component,
                "props": spec.get("props") or {},
                "events": spec.get("events") or {},
            }
        elif rtype == "code":
            payload = {
                "lang": spec.get("lang") or "jsx",
                "source": spec.get("source") or "",
                "events": spec.get("events") or {},
            }
        elif rtype == "data":
            payload = {"data": spec.get("data")}
        elif rtype == "close":
            pass
        else:
            raise ValueError(f"未知渲染类型: {rtype}")

        if spec.get("title"):
            payload["title"] = spec["title"]
        base["payload"] = payload
        return base