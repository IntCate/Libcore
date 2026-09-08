# UI 界面

> 从零开始理解 libcore 的 UI 能力：agent 如何决定渲染什么，前端如何与 agent 交互。

## 1. 什么是 UI 能力

**UI** 是系统能力节点：agent 具备**渲染能力**——它决定渲染什么界面、用什么组件、怎么布局、绑什么数据，产出**渲染指令**（数据契约）。渲染执行在节点内部（或交给渲染层），agent 不碰像素。

产出约定：`data["render"]` 为渲染指令（组件/布局/数据契约）。无渲染后端 → 降级为 not\_implemented（诚实披露，绝不编造渲染结果）。

## 2. UI 能力节点

manifest 暴露 `ui` 一个入口，op 为 `find` / `render`：

| op       | 作用                                       |
| -------- | ---------------------------------------- |
| `find`   | 返回前端当前可用组件清单（渐进披露，供 agent 决策选组件，省 token） |
| `render` | 决定组件/布局/数据，产出渲染指令                        |

### 装配

```python
from libcore.plugins.capabilities import ui
ui.register(bus, renderer=my_renderer, manifest_store=my_manifest_store, channels=channels)
```

- `renderer`：真实渲染后端（签名 `renderer(spec) -> dict`）；缺省占位降级。

- `manifest_store`：前端组件清单存储（`snapshot() -> {"components": [...]}`），使 `ui find` 能向 agent 披露"前端现在有哪些组件可用"。

- `channels`：通道注册表。当 payload 带 `channel_id` 时，render 优先经 `Channel.reply` 送回原会话（统一出站链路）。

## 3. UI 桥接（UiBridge）

`libcore/ui_bridge/bridge.py` 的 `UiBridge` 是应用层装配，把前端、传输引擎、agent 接起来。

### 端点一览

| 端点                     | 作用                                            |
| ---------------------- | --------------------------------------------- |
| `GET /`                | 前端 SPA（mount\_static，单端口同源）                   |
| `POST /api/rpc`        | 上行 RPC：前端事件 → bus.dispatch → CapabilityResult |
| `POST /api/manifest`   | 前端上报组件清单                                      |
| `GET /api/ui/manifest` | 后端查询前端可用组件清单                                  |
| `POST /api/ui/render`  | 手动触发渲染（经 ui 节点 → RenderSink → WS 下行）          |
| `WS /events`           | 渲染/数据下行通道                                     |

### 装配

```python
bridge = UiBridge()
bridge.attach_agent(agent)          # 挂接 agent：共享总线，ui 能力进入 agent manifest
bridge.attach_kernel(kernel)        # 挂接常驻内核：入站走统一链路
bridge.attach_feishu(app_id, app_secret)  # 挂接飞书 IM 入站监听
bridge.start_feishu_listener()      # 后台线程启动飞书 WebSocket
```

## 4. UI 通道（统一通讯机制）

UI 是统一通讯机制的一个通道。前端用户消息经 `UiChannel.ingest` 广播进统一链路：

```
前端聊天框 onSend
  → run 能力节点（POST /api/rpc → bus.dispatch("run")）
  → UiChannel.ingest → 广播 channel.inbound
  → 薄桥接 → ResidentKernel → AgentLoop 推理
  → agent dispatch("ui", op="render", {channel_id, text})
  → ui 能力经 Channel.reply → RenderSink → WS 下行回前端
```

`channel_id = ui:{session_id}`，同一前端会话保持同一 channel\_id。

## 5. 渲染下行

`RenderSink` 把渲染指令经 WebSocket 下行到前端：

```python
sink = RenderSink(engine, ws)
sink({"type": "render", "component": "chat", "props": {"text": "..."}})
```

`RenderSink` 是可调用对象：`sink(spec)` 构建渲染消息并 push 到 WebSocket。`spec` 字段见 `libcore/ui_bridge/render_sink.py` 的 `build`（`type` / `component` / `props` / `target` / `events` / `data` / `lang` / `source` / `title`）。

## 6. 代码位置

- `libcore/plugins/capabilities/ui.py`：UI 能力节点

- `libcore/ui_bridge/bridge.py`：UiBridge 装配

- `libcore/ui_bridge/manifest_store.py`：ManifestStore

- `libcore/ui_bridge/render_sink.py`：RenderSink

- `libcore/ui_bridge/server.py`：服务启动

- `libcore/channels/ui_channel.py`：UiChannel

