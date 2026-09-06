# 统一通讯机制（Channel）

> 从零开始理解 libcore 如何用 Channel 抽象统一 UI / IM / CLI 三个外部入口。

## 1. 核心洞察

IM、UI、CLI 本质是**同一件事**：都是"外部入口发起对话 → agent 处理 → 回复送回原处"。唯一区别是**入口形态**（飞书会话 / 网页聊天框 / 终端），而"消息从哪来回哪去"的**链路是同一套**。

## 2. Channel 接口

```python
class Channel(ABC):
    @property
    def channel_id(self) -> str: ...   # 唯一通道 id（即 session_id）
    @property
    def kind(self) -> str: ...         # im / ui / cli
    def ingest(self, message) -> None: # 入站：广播进总线
    def reply(self, message) -> None:  # 出站：送回原会话
```

## 3. 三个通道

| 通道 | channel_id | 入站 | 出站 |
| --- | --- | --- | --- |
| IM | `im:{platform}:{chat_id}` | 平台监听（飞书 WebSocket 等） | 复用 im.py send |
| UI | `ui:{session_id}` | 前端聊天框 onSend | 复用 ui.py render |
| CLI | `cli:stdin` | 终端输入 | 打印到终端 |

## 4. 完整链路

```
外部入口 → Channel.ingest → 广播 channel.inbound
  → 薄桥接 wire_inbound_to_kernel → ResidentKernel.submit
  → AgentLoop 推理 → dispatch("im"/"ui"/"cli", {channel_id, text})
  → 能力节点经 Channel.reply 送回原会话
```

## 5. 关键设计

- **channel_id == session_id**：是"字符串约定"而非"对象耦合"。SessionStore 只认 id，来源无关——同一个会话（channel_id）可以跨 UI 和 IM 迁移。
- **内核零改动**：只依赖 EventBus 的 publish/dispatch 两个原语。
- **不新增调度器**：复用现有 `ResidentKernel`，薄桥接几行代码。
- **插件层只动自己**：UI/IM/CLI 各自实现 Channel，互不依赖。
- **装配层配置驱动**：加通道只改配置，不改内核。

## 6. 代码位置

- `libcore/channels/base.py`：Channel 抽象
- `libcore/channels/message.py`：InboundMessage / OutboundMessage
- `libcore/channels/registry.py`：ChannelRegistry
- `libcore/channels/ui_channel.py`：UiChannel
- `libcore/channels/im_channel.py`：ImChannel
- `libcore/channels/cli_channel.py`：CliChannel
- `libcore/channels/bridge.py`：wire_inbound_to_kernel 薄桥接
- `libcore/channels/feishu_listener.py`：FeishuListener
