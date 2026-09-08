"""UI 桥接装配：建 bus + 复用传输引擎 + 注入 renderer + 注册 HTTP/WS/静态路由。

对齐传输引擎集成（libcore/engine/transport/）的骨架，但把 ui 能力节点从"占位降级"换为
真实 RenderSink，并新增手动触发端点 ``POST /api/ui/render`` 与 manifest 双向。

端点一览：
- GET  /                -> 前端 SPA（mount_static，单端口同源）
- POST /api/rpc         -> 上行 RPC：前端事件 -> bus.dispatch -> CapabilityResult
- POST /api/manifest    -> 前端上报组件清单
- GET  /api/ui/manifest -> 后端查询前端可用组件清单（供 ui 能力披露给 agent）
- POST /api/ui/render   -> 手动触发渲染（经 ui 节点 -> RenderSink -> WS 下行）
- WS   /events          -> 渲染/数据下行通道
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
from typing import Any, Dict

import uvicorn

_logger = logging.getLogger("libcore.ui_bridge")

from libcore.kernel.bus import Dispatch, EventBus, Notice
from libcore.engine.transport.fastapi_engine import FastAPITransportEngine
from libcore.channels import ChannelRegistry

from .manifest_store import ManifestStore
from .render_sink import RenderSink

PORT = 5000
# 前端构建产物：web/web_dist/（相对本文件定位到项目根）
WEB_DIST = pathlib.Path(__file__).resolve().parents[2] / "web" / "web_dist"


class UiBridge:
    """应用层装配。构造后暴露 ``app``（FastAPI app）供启动。"""

    def __init__(self, dist_dir: str | os.PathLike | None = None) -> None:
        self.bus = EventBus()
        self.engine = FastAPITransportEngine()
        self.manifests = ManifestStore()

        # ---- 第三层：挂接的 Agent（与桥共享总线，经 ui 能力渲染到前端）----
        self.agent: Any = None
        self._reason = None
        # 前端事件回传日志（run 能力落点，可观测闭环）
        self._event_log: list = []
        # 通道注册表：UI 会话 → UiChannel（统一出站链路）
        self.channels = ChannelRegistry()
        # 常驻内核（可选挂接）：入站走统一链路时由薄桥接接到它
        self._kernel: Any = None
        # 飞书入站监听器（可选挂接）：WebSocket 收消息 → ImChannel.ingest
        self._feishu_listener: Any = None

        # ---- 1. 传输通道 ----
        self._ws = self.engine.open({"type": "websocket", "path": "/events"})

        # ---- 2. 注入渲染出口（ui 能力节点的真实 renderer）----
        self.sink = RenderSink(self.engine, self._ws)

        # ---- 3. 装配能力 ----
        self._wire_ui_capability()
        self._wire_run_capability()

        # ---- 4. 注册路由 + 挂载静态 ----
        self._wire_routes()
        self._wire_agent_routes()
        self._mount_static(dist_dir or WEB_DIST)

    # ---- 能力装配 ----

    def _wire_ui_capability(self) -> None:
        from libcore.plugins.capabilities import ui as ui_capability
        # 注入 manifest_store：ui find 能向 agent 披露前端当前可用组件清单
        # 注入 channels：ui render 带 channel_id 时经 Channel.reply 送回原会话（统一出站）
        ui_capability.register(self.bus, renderer=self.sink,
                               manifest_store=self.manifests, channels=self.channels)

    def _wire_run_capability(self) -> None:
        """注册 run 能力：承接前端组件事件回传（POST /api/rpc -> bus.dispatch("run")）。

        前端组件事件（如 chat 的 onSend）绑定到 target="run"，事件经上行 RPC 落到这里。
        默认实现：若事件是"用户发送消息"（payload 带 text），构造 InboundMessage 走
        UiChannel.ingest（统一入站链路）；否则记录并回显（可观测、可验证闭环）。
        开发者可覆盖为真实业务处理。
        """
        from libcore.kernel.bus import CapabilityResult

        def handle(d: Dispatch) -> CapabilityResult:
            event = d.payload.get("event")
            source = d.payload.get("source")
            self._event_log.append({"source": source, "event": event, "op": d.op})
            # 用户发送消息 → 统一入站链路（UiChannel.ingest → channel.inbound 广播）
            text = d.payload.get("text")
            if text:
                self._ingest_ui_message(text, d.payload.get("session_id"))
                return CapabilityResult(ok=True, data={"received": event, "source": source,
                                                       "status": "ingested"})
            return CapabilityResult(ok=True, data={"received": event, "source": source})

        self.bus.on("run", handle, meta={
            "description": "系统能力节点：承接前端组件事件回传（事件上行 RPC 的落点）。"
                           "收到事件后记录并回显，供业务处理或闭环验证。",
            "ops": ["run"],
        })

    def _ingest_ui_message(self, text: str, session_id: str | None = None) -> None:
        """把前端用户消息规整成 InboundMessage，经 UiChannel.ingest 广播进统一链路。

        - 按 session_id 复用/创建 UiChannel（同一前端会话保持同一 channel_id）。
        - 未挂接常驻内核时，广播仍发出（薄桥接未接线则无人消费，无副作用）。
        """
        from libcore.channels.ui_channel import UiChannel
        from libcore.channels.message import InboundMessage

        sid = session_id or "default"
        channel_id = f"ui:{sid}"
        channel = self.channels.get(channel_id)
        if channel is None:
            channel = UiChannel(self.bus, session_id=sid, sink=self.sink)
            self.channels.register(channel)
        channel.ingest(InboundMessage(
            channel_id=channel_id, kind="ui", platform="ui",
            user_id="web-user", text=text,
        ))

    # ---- 路由 ----

    def _wire_routes(self) -> None:
        self.engine.route("/api/rpc", self._rpc_handler)
        self.engine.route("/api/manifest", self._manifest_upsert)
        self.engine.route("/api/ui/manifest", self._manifest_query, method="GET")

        # 手动触发渲染端点：POST /api/ui/render，body 为渲染语义参数
        # （{type, target, component, props, ...}）。经 ui 节点 -> RenderSink -> WS 下行。
        async def _render_trigger(request: dict) -> dict:
            return await self._dispatch("ui", "render", request.get("payload", request))

        self.engine.route("/api/ui/render", _render_trigger)

    def _mount_static(self, dist_dir) -> None:
        self.engine.mount_static(str(dist_dir))

    # ---- 上行 RPC：前端事件 -> bus ----

    async def _rpc_handler(self, request: dict) -> dict:
        target = request.get("target")
        op = request.get("op") or "run"
        payload = request.get("payload", {})
        rpc_id = request.get("rpcId")
        if not target:
            return {"rpcId": rpc_id, "ok": False, "error": "缺少 target"}
        try:
            result = await self.bus.dispatch(Dispatch(target=target, op=op, payload=payload))
        except Exception as e:  # noqa: BLE001 - 桥接层兜底，失败返回结构化错误
            return {"rpcId": rpc_id, "ok": False, "error": str(e)}
        return {"rpcId": rpc_id, "ok": result.ok, "data": result.data, "error": result.error}

    # ---- 统一 dispatch（供上行 RPC / 手动渲染复用）----

    async def _dispatch(self, target: str, op: str, payload: dict) -> dict:
        try:
            result = await self.bus.dispatch(Dispatch(target=target, op=op, payload=payload))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "data": {}, "error": str(e)}
        return {"ok": result.ok, "data": result.data, "error": result.error}

    # ---- manifest 双向 ----

    async def _manifest_upsert(self, request: dict) -> dict:
        components = request.get("components", [])
        self.manifests.update(components)
        # 广播：清单变更，agent 可感知
        await self.bus.publish(Notice(topic="ui.manifest_changed",
                                      payload={"components": components}))
        return {"ok": True, "data": self.manifests.snapshot()}

    async def _manifest_query(self, request: dict) -> dict:
        return {"ok": True, "data": self.manifests.snapshot()}

    # ---- 第三层：agent 集成 ----

    def attach_agent(self, agent) -> None:
        """把 agent 挂到桥：与桥共享同一总线，复用 agent 的决策推理器。

        - ``agent._bus = self.bus``：让桥的 ``ui`` 能力进入 agent 可呼叫清单（manifest），
          agent 才能点名它渲染到前端；
        - ``agent._reason`` 复用：桥不在 uvicorn 事件循环外再跑 ``asyncio.run``，
          而是复用同一推理器，在桥的循环内驱动 AgentLoop。
        ```

        注意：agent 内部也有一个自己的默认 EventBus；挂接后改为共用桥的总线，
        之后 ``agent.capability(...)`` 注册的能力也直接落在共享总线上（可被前端 RPC 调用）。
        """
        self.agent = agent
        agent._bus = self.bus          # 共享总线：ui 能力进入 agent manifest
        self._reason = agent._reason    # 复用同一决策推理器

    def attach_kernel(self, kernel) -> None:
        """把常驻内核挂到桥：入站走统一链路（薄桥接 channel.inbound → kernel.submit）。

        - 复用现有 ResidentKernel 作为调度核心，**不新增调度器**；
        - 前端用户消息经 UiChannel.ingest 广播 channel.inbound，薄桥接接到内核驱动 AgentLoop；
        - 内核与桥共享同一总线，agent 决策结果经 ui 能力渲染回前端。
        """
        from libcore.channels.bridge import wire_inbound_to_kernel

        self._kernel = kernel
        kernel.bus = self.bus          # 共享总线：入站广播与出站点名同一条
        wire_inbound_to_kernel(self.bus, kernel)

    def attach_feishu(self, app_id: Optional[str] = None, app_secret: Optional[str] = None,
                      domain: str = "feishu") -> None:
        """把飞书 IM 入站监听挂到桥：WebSocket 收消息 → ImChannel.ingest → 统一入站链路。

        - 复用 ``im`` 能力节点的 ``wire_feishu_listener`` 装配监听器；
        - 收到消息经 ImChannel.ingest 广播 channel.inbound，与 UI 通道走同一条链路；
        - 出站 sender 复用飞书适配器（FeishuAdapter）发回原会话；
        - 监听器需在后台线程/任务 ``start()``（WebSocket 长连接阻塞）。

        密钥来源（装配层便利，不约束插件）：
        - 显式传入 ``app_id`` / ``app_secret`` 优先；
        - 缺省时经统一工厂 ``build_adapter`` 从密钥层读取 ``FEISHU_APP_ID`` / ``FEISHU_APP_SECRET``；
        - 两者皆无则抛错（诚实披露，不静默降级）。
        """
        from libcore.plugins.capabilities import im as im_capability
        from libcore.plugins.capabilities.platforms import build_adapter

        adapter = build_adapter("feishu", app_id=app_id, app_secret=app_secret, domain=domain)

        self._feishu_listener = im_capability.wire_feishu_listener(
            self.bus, self.channels, app_id=app_id or "", app_secret=app_secret or "",
            domain=domain, adapter=adapter,
        )

    def start_feishu_listener(self) -> None:
        """在后台线程启动飞书监听（WebSocket 长连接阻塞，需独立线程）。"""
        if self._feishu_listener is None:
            raise RuntimeError("未装配飞书监听：请先调用 UiBridge.attach_feishu(app_id, app_secret)")
        import threading
        threading.Thread(target=self._feishu_listener.start, daemon=True).start()

    def _wire_agent_routes(self) -> None:
        """agent 调度入口：POST /api/agent/submit -> 在桥的事件循环内后台驱动 AgentLoop。"""
        async def _agent_submit(request: dict) -> dict:
            if self.agent is None:
                return {"ok": False, "error": "未挂接 agent：请先调用 UiBridge.attach_agent(agent)"}
            goal = request.get("goal")
            if not goal:
                return {"ok": False, "error": "缺少 goal"}
            # 只入队、不阻塞 RPC：后台任务驱动 agent，能力经同总线渲染到前端
            asyncio.create_task(self._drive_agent_goal(goal))
            return {"ok": True, "data": {"goal": goal, "status": "scheduled"}}

        self.engine.route("/api/agent/submit", _agent_submit)

    @staticmethod
    def _agent_input_nodes():
        """从 capabilities.yaml 读取决策输入节点；未注册时环内静默降级为最简默认。"""
        from libcore.plugins.loader import CapabilityLoader
        return CapabilityLoader.load_input_nodes() or [
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ]

    async def _drive_agent_goal(self, goal: str) -> None:
        """在桥的事件循环内复用 AgentLoop 驱动 agent；agent 决策结果经同总线渲染。

        产物：agent 若决定调用 ``ui``，则 ``RenderSink`` 会把渲染指令经 WS 下行到前端。
        """
        from libcore.kernel.agent import AgentLoop
        loop = AgentLoop(self.bus, self._reason, input_nodes=self._agent_input_nodes())
        try:
            ctx = await loop.run({"goal": goal})
            _logger.info("agent goal done: %s done=%s obs=%d",
                         goal, ctx.done, len(ctx.observations))
        except Exception as e:  # noqa: BLE001 - 驱动层兜底，避免后台任务静默消失
            _logger.error("agent goal failed: %s -> %s", goal, e)
            await self.bus.publish(Notice(topic="agent.goal_failed",
                                          payload={"goal": goal, "error": str(e)}))

    # ---- 启动 ----

    @property
    def app(self):
        return self.engine.app_obj()


def run(dist_dir: str | os.PathLike | None = None, host: str = "127.0.0.1", port: int = PORT) -> None:
    """启动 UI 桥接服务。"""
    bridge = UiBridge(dist_dir)
    config = uvicorn.Config(bridge.app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    print(f"\n[ui-bridge] 启动完成：http://localhost:{port}/  (单端口同源)")
    print(f"[ui-bridge] 上行 RPC  : POST /api/rpc")
    print(f"[ui-bridge] 手动渲染  : POST /api/ui/render")
    print(f"[ui-bridge] 组件清单  : GET  /api/ui/manifest")
    print(f"[ui-bridge] 下行通道  : ws://localhost:{port}/events")
    server.run()