"""libcore.ui_bridge —— UI 第二层：前后端桥接（单端口同源）。

职责：把第一层前端消费端与后端 ui 能力节点打通。
- 渲染下行：ui 节点产出渲染指令 -> RendeSinkEngine.push -> WebSocket -> 前端 consumeRender
- 事件上行：前端 emit -> POST /api/rpc -> bus.dispatch -> 后端能力处理
- manifest 双向：前端 POST /api/manifest 上报组件清单；后端 GET /api/ui/manifest 查询

复用 libcore 传输引擎（正式路径 libcore/engine/transport/）的 FastAPITransportEngine
（单端口 HTTP + WebSocket + 静态），装配逻辑对齐集成（ui_bridge/server.py），
注入真实 ui renderer（replace 占位降级）。
"""