# Session 会话存储

> 从零开始理解 libcore 的会话存储：如何按 session\_id / chat\_id 读写完整会话。

## 1. 什么是 Session

**Session（会话）** 是完整实体（内嵌消息列表）。libcore 用 `SessionStore` 契约读写会话，context 等插件依赖本契约，不关心底层是 SQLite / 文件 / 内存 / 远程。

## 2. 会话记录

```python
@dataclass
class AgentSessionRecord:
    id: str
    chat_id: str
    created_at: str = ""
    updated_at: str = ""
    graph_state: Optional[Dict[str, Any]] = None
    current_node: str = ""
    step_count: int = 0
    messages: List[MessageRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
```

消息元素用 `MessageRecord`：

```python
@dataclass
class MessageRecord:
    id: str
    chat_id: str
    role: str
    message_type: str = "normal"
    content: str = ""
    reasoning_content: Optional[str] = None
    created_at: str = ""
    model: Optional[str] = None
    files: List[Any] = field(default_factory=list)
    agent_session_id: Optional[str] = None
    agent_node: Optional[str] = None
    agent_step: Optional[int] = None
    agent_metadata: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
```

## 3. SessionStore 契约

```python
class SessionStore(ABC):
    def get_agent_session(self, session_id) -> Optional[AgentSessionRecord]: ...
    def save_agent_session(self, session: AgentSessionRecord) -> None: ...
    def list_agent_sessions(self, chat_id) -> List[AgentSessionRecord]: ...
    def delete_agent_session(self, session_id) -> None: ...
```

契约只定义"存什么、取什么"（数据形状），不定义"怎么存"。实现方负责把 dataclass 转成自己的存储格式（SQL 行 / JSON 文件 / 远程 API），并转回 dataclass。

## 4. 与 Channel 的关系

**channel\_id == session\_id**：是"字符串约定"而非"对象耦合"。SessionStore 只认 id，来源无关——同一个会话（channel\_id）可以跨 UI 和 IM 迁移。

## 5. 与 Context 的关系

context 输入节点依赖 SessionStore 契约：当 payload 带 `session_id` 且注入 `backend` 时，优先从 backend 读历史；否则回退到 `payload["session_messages"]`。

```python
from libcore.plugins.capabilities import context
context.register(bus, backend=session_store)
```

## 6. 存储实现

libcore 提供多种存储实现（`libcore/storage/`）：

| 实现                  | 说明           |
| ------------------- | ------------ |
| `SessionStore`      | 会话存储契约（抽象接口） |
| `SqliteDataStore`   | SQLite 数据存储  |
| `SqliteMemoryStore` | SQLite 记忆存储  |
| `SqliteStateStore`  | SQLite 状态存储  |
| `LocalFileStore`    | 本地文件存储       |

## 7. 代码位置

- `libcore/storage/contracts.py`：AgentSessionRecord / MessageRecord / CheckpointRecord

- `libcore/storage/session_store.py`：SessionStore 契约

- `libcore/storage/`：存储实现

