"""FastAPI 传输引擎实现（正式路径，由 transport_verify/fastapi_engine.py 归档演进而来）。

要点：
- 单端口同源：一个 FastAPI app 同时提供 HTTP 路由 / WebSocket / 静态文件。
- route() 注册的 handler 为 async (request: dict) -> dict；支持指定 method（GET 时以空 dict 调用）。
- open({type:"websocket", path}) 注册一个 WebSocket 端点，push() 向它下行数据；
- mount_static() 挂载前端文件，浏览器打开根路径即拿到页面。

注：为保证 WebSocket 端点能收到 push，本实现按 path 保存活跃连接列表；
连接生命周期可进一步拆 open/close 管理，当前保持最小。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.routing import WebSocketRoute

from .transport_engine import TransportEngine


class FastAPITransportEngine(TransportEngine):
    def __init__(self) -> None:
        self.app = FastAPI()
        self._routes: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {}
        self._ws_subscribers: Dict[str, List[WebSocket]] = {}
        self._channel_counter = 0

    # ---- route：注册 HTTP 路由 ----
    def route(self, path: str, handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
              method: str = "POST") -> None:
        """注册 HTTP 路由。``method`` 默认 POST；GET 时把 query 参数作为 request 传入。"""
        self._routes[path] = handler

        async def _http(request: dict) -> Any:
            try:
                result = await handler(request or {})
            except Exception as e:  # noqa: BLE001 - 桥接层兜底，失败返回结构化错误
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
            return result

        if method.upper() == "GET":
            # GET 无 body：以空 dict 调用 handler（需参数的 GET 请改用 query 透传，此处保持最简）。
            async def _get() -> Any:
                return await _http({})

            self.app.get(path)(_get)
        else:
            self.app.post(path)(_http)

    # ---- open：开通道 ----
    def open(self, spec: Dict[str, Any]) -> str:
        stype = spec["type"]
        if stype == "websocket":
            path = spec.get("path", f"/ws/{self._channel_counter}")
            self._channel_counter += 1
            chid = f"ws://{path}"

            # 用 WebSocketRoute 动态追加端点
            async def _ws(websocket: WebSocket) -> None:
                await websocket.accept()
                self._ws_subscribers.setdefault(chid, []).append(websocket)
                try:
                    while True:
                        await websocket.receive_text()  # 保持心跳（纯下行，客户端只收不发）
                except Exception:  # noqa: BLE001 - 客户端断开
                    pass
                finally:
                    try:
                        self._ws_subscribers.get(chid, []).remove(websocket)
                    except ValueError:
                        pass

            self.app.router.routes.append(WebSocketRoute(path, _ws))
            return chid
        raise NotImplementedError(f"暂不支持通道类型: {stype}")

    def close(self, id: str) -> None:
        # 最小验证：断开并清空该通道的所有订阅连接
        for ws in self._ws_subscribers.pop(id, []):
            try:
                asyncio.to_thread(ws.close)  # 不阻塞；真实实现应走并发协程管理
            except Exception:  # noqa: BLE001
                pass

    @property
    def ws_routes(self) -> list:
        return list(self._ws_subscribers.keys())

    # ---- push：主动下行（WebSocket）----
    def push(self, channel: str, data: Dict[str, Any]) -> None:
        for ws in list(self._ws_subscribers.get(channel, [])):
            try:
                asyncio.get_running_loop().create_task(ws.send_json(data))
            except Exception:  # noqa: BLE001 - 单连接故障不拖垮整体
                continue

    # ---- mount_static：单端口同源 ----
    def mount_static(self, dist_dir: str) -> None:
        self.app.mount("/", StaticFiles(directory=dist_dir, html=True), name="spa")
        # fallback：非文件请求回 index.html 交给 SPA
        @self.app.get("/{path:path}")
        async def _spa_fallback(path: str):
            from fastapi.responses import FileResponse
            import os
            index = os.path.join(dist_dir, "index.html")
            if os.path.exists(index):
                return FileResponse(index)
            raise HTTPException(status_code=404, detail="前端未构建")

    def app_obj(self) -> FastAPI:
        return self.app