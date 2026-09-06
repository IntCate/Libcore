# 事件总线与通讯机制

> 从零开始理解 libcore 的通讯核心：EventBus 如何用"点名 + 广播 + 横切面"三个原语支撑整个 agent 系统。

## 1. 核心法则

> **总线的职责止步于"投递"，决策的职责止步于 Agent。**

总线不判断"下一步该干嘛"。它只负责三件事：

1. **点名（Dispatch）**：按 target 定向查表直投，不做谓词匹配（消灭隐性流程）。
2. **广播（Notice）**：按 topic 扇出，用于系统级感知。
3. **横切面（Aspect）**：对所有流过的信号统一拦截（日志 / 护栏 / 追踪）。

## 2. 两个事件原语

### Dispatch（点名）

Agent 业务调度用。携带目标地址 `target`，由总线定向直投到**唯一** handler。

```python
@dataclass(frozen=True)
class Dispatch:
    target: str                    # 目标能力名（如 "skill" / "tools" / "im"）
    op: str                        # 操作（如 "find" / "read" / "run"）
    payload: Dict[str, Any]        # 参数
    cid: CorrelationId             # 全链路因果 id
    parent_cid: Optional[CorrelationId] = None
```

### Notice（广播）

系统级通知 / 感知用。按 topic 扇出到**所有**订阅者。

```python
@dataclass(frozen=True)
class Notice:
    topic: str                     # 主题（如 "channel.inbound" / "capability.changed"）
    payload: Dict[str, Any]
    cid: CorrelationId
```

### 区别

| <br /> | Dispatch                         | Notice                      |
| ------ | -------------------------------- | --------------------------- |
| 语义     | 点名（定向）                           | 广播（扇出）                      |
| 查表     | `_handlers`（target → 唯一 handler） | `_subs`（topic → 多个 handler） |
| 用途     | Agent 业务调度                       | 系统级感知                       |
| 无目标    | 抛 `NoHandlerError`               | 静默（无订阅者）                    |

## 3. 总线 API

### 注册（装配）

```python
bus.on("skill", handle, meta={"description": "...", "ops": ["find", "read"]})
# 同名 target 覆盖；meta 登记能力"被发现"所需的描述与 schema

bus.off("skill")          # 注销能力（热更新下线）
bus.manifest()            # 生成可呼叫清单 [{target, ...meta}]
bus.subscribe("channel.inbound", handler)   # 订阅广播主题
```

### 投递

```python
await bus.dispatch(Dispatch(target="skill", op="find", payload={}))
# 点名：按 target 直投到唯一 handler，兼容同步/异步

await bus.publish(Notice(topic="channel.inbound", payload={...}))
# 广播：按 topic 扇出到所有订阅者
```

### 横切面

```python
class Aspect(ABC):
    def matches(self, signal) -> bool: ...   # 过滤条件
    async def before(self, signal) -> None: ...  # 投递前（护栏/日志）
    async def after(self, signal, result) -> None: ...  # 投递后（追踪/归一）

bus.add_aspect(aspect)      # 挂载（顺序 = 挂载顺序）
bus.remove_aspect(aspect)   # 卸载
```

**横切面拦截顺序**：`before` 正序（先套先跑），`after` 逆序（后套先跑）。
`before` 若返回非 None（CapabilityResult），视为**护栏阻断**，跳过 handler。

## 4. 生命周期记账（Ledger）

总线对"非信号"事件（`bus.on/off`、横切面挂载、异常护栏）顺手记一笔，默认写标准 logging（DEBUG 级）。可注入自定义 ledger 替换输出端，内核无需改动。

```python
bus = EventBus(ledger=lambda event: my_sink(event))
```

## 5. 统一通讯机制（Channel）

IM、UI、CLI 本质是**同一件事**：都是"外部入口发起对话 → agent 处理 → 回复送回原处"。libcore 用 **Channel 抽象**统一它们。

### Channel 接口

```python
class Channel(ABC):
    @property
    def channel_id(self) -> str: ...   # 唯一通道 id（即 session_id）
    @property
    def kind(self) -> str: ...         # im / ui / cli
    def ingest(self, message) -> None: # 入站：广播进总线
    def reply(self, message) -> None:  # 出站：送回原会话
```

### 三个通道

| 通道  | channel\_id               | 入站                   | 出站              |
| --- | ------------------------- | -------------------- | --------------- |
| IM  | `im:{platform}:{chat_id}` | 平台监听（飞书 WebSocket 等） | 复用 im.py send   |
| UI  | `ui:{session_id}`         | 前端聊天框 onSend         | 复用 ui.py render |
| CLI | `cli:stdin`               | 终端输入                 | 打印到终端           |

### 完整链路

```
外部入口 → Channel.ingest → 广播 channel.inbound
  → 薄桥接 wire_inbound_to_kernel → ResidentKernel.submit
  → AgentLoop 推理 → dispatch("im"/"ui"/"cli", {channel_id, text})
  → 能力节点经 Channel.reply 送回原会话
```

### 关键设计

- **channel\_id == session\_id**：是"字符串约定"而非"对象耦合"，SessionStore 只认 id，来源无关。

- **内核零改动**：只依赖 EventBus 的 publish/dispatch 两个原语。

- **不新增调度器**：复用现有 `ResidentKernel`，薄桥接几行代码。

- **插件层只动自己**：UI/IM/CLI 各自实现 Channel，互不依赖。

## 6. 代码位置

- `libcore/kernel/bus/bus.py`：EventBus 实现

- `libcore/kernel/bus/event.py`：Dispatch / Notice / CapabilityResult / CorrelationId

- `libcore/channels/`：Channel 抽象 + UI/IM/CLI 通道 + 薄桥接

