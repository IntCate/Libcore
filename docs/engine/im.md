# IM 即时通讯

> 从零开始理解 libcore 的 IM 能力：agent 如何收发 IM 消息，如何接入飞书/微信/Slack/Telegram 等平台。

## 1. 什么是 IM 能力

**IM** 是系统能力节点：agent 接管系统核心后，**IM 入口**是 agent 可调度的能力节点。agent 决定收发什么消息、发到哪个平台/会话。

产出约定：`data["status"]` 为发送结果；`data["message_id"]` 为平台消息 id（可选）。无平台适配器 → 降级为 not\_implemented（诚实披露，绝不编造发送成功）。

**内部细节暴露**：平台适配器是**开放组件，不走总线**（保持零耦合可拆卸）。但 `im` 门面会把内部路由细节塞进 `result.data["_internal"]`（`platform` / `adapter_op`），随结果上抛，使 tracing/logging 能还原到具体平台这一层，而不只是 `im` 门面。

## 2. IM 能力节点

manifest 暴露 `im` 一个入口，op 为 `send` / `receive` / `list_platforms` / `status`：

| op               | 作用          |
| ---------------- | ----------- |
| `send`           | 发消息到指定平台/会话 |
| `receive`        | 收消息         |
| `list_platforms` | 列出已接线平台     |
| `status`         | 查询状态        |

### 装配

```python
from libcore.plugins.capabilities import im
im.register(bus, registry=im_registry, channels=channels)
```

- `registry`：平台适配器注册表（`IMRegistry`），每个平台适配器签名 `adapter(op, payload) -> dict`。

- `channels`：通道注册表。当 payload 带 `channel_id` 时，send 优先经 `Channel.reply` 送回原会话（统一出站链路）。

## 3. 平台适配器注册表（IMRegistry）

```python
class IMRegistry:
    def register(self, platform, adapter) -> None: ...   # 注册平台适配器
    def get(self, platform) -> Optional[IMAdapter]: ...
    def platforms(self) -> List[str]: ...
    def dispatch(self, platform, op, payload) -> dict: ...  # 按平台路由
```

`im` 节点按 `payload["platform"]` 路由到对应适配器。未注册平台抛 `NotImplementedError`（诚实披露）。

## 4. 平台适配器

libcore 提供多个平台适配器（`libcore/plugins/capabilities/platforms/`）：

| 平台       | 适配器           | 状态                           |
| -------- | ------------- | ---------------------------- |
| 飞书       | `feishu.py`   | 完整 send（lark\_oapi）          |
| 微信       | `weixin.py`   | 完整 send（iLink 协议）            |
| Slack    | `slack.py`    | 完整 send（slack\_sdk）          |
| Telegram | `telegram.py` | 完整 send（python-telegram-bot） |
| 钉钉       | `dingtalk.py` | 完整 send（静态 webhook）          |
| QQ       | `qqbot.py`    | 完整 send（REST API）            |
| 企业微信     | `wecom.py`    | 骨架（send 依赖 WebSocket）        |
| 元宝       | `yuanbao.py`  | 骨架（send 依赖 WebSocket）        |

每个适配器实现 `__call__(op, payload) -> dict` 契约，惰性降级：依赖未安装时模块仍可导入，装配方未接线该平台即降级 not\_implemented。

## 5. IM 通道（统一通讯机制）

IM 是统一通讯机制的一个通道。平台消息经 `ImChannel.ingest` 广播进统一链路：

```
平台收到消息（飞书 WebSocket 等）
  → FeishuListener 解析 (chat_id, user_id, text)
  → ImChannel.ingest → 广播 channel.inbound
  → 薄桥接 → ResidentKernel → AgentLoop 推理
  → agent dispatch("im", op="send", {channel_id, text})
  → im 能力经 Channel.reply → 平台适配器发回原会话
```

`channel_id = im:{platform}:{chat_id}`，同一平台会话保持同一 channel\_id。

## 6. 飞书入站监听

`wire_feishu_listener` 装配飞书入站监听（WebSocket 收消息 → ImChannel.ingest）：

```python
from libcore.plugins.capabilities import im
listener = im.wire_feishu_listener(bus, channels, app_id, app_secret, domain="feishu")
listener.start()   # 后台线程/任务启动（WebSocket 长连接阻塞）
```

依赖 `lark-oapi`；未安装时抛 RuntimeError（诚实披露，不静默降级）。

## 7. 完整装配示例

```python
bridge = UiBridge()
bridge.attach_kernel(kernel)              # 入站走统一链路
bridge.attach_feishu(app_id, app_secret)  # 飞书入站监听
bridge.start_feishu_listener()            # 后台线程启动 WebSocket
```

## 8. 代码位置

- `libcore/plugins/capabilities/im.py`：IM 能力节点 + IMRegistry + wire\_feishu\_listener

- `libcore/plugins/capabilities/platforms/`：平台适配器

- `libcore/channels/im_channel.py`：ImChannel

- `libcore/channels/feishu_listener.py`：FeishuListener

