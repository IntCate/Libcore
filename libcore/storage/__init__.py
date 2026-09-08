"""存储纯库：实体契约 + SQLite 数据/状态存储 + 本地文件存储。

对齐旧 ``app.runtime.kernel.storage_components``，但按 libcore 收敛为纯库模块
（标准库 sqlite3，零 SQLAlchemy 依赖），供 memory / context / knowledge 持久化使用。
"""
from libcore.storage.contracts import (
    AgentSessionRecord,
    CheckpointRecord,
    MemoryRecord,
    MessageRecord,
    SessionRecord,
)
from libcore.storage.memory_store import MemoryBackend
from libcore.storage.session_store import SessionStore
from libcore.storage.sqlite_store import SqliteDataStore
from libcore.storage.sqlite_memory_store import SqliteMemoryStore
from libcore.storage.state_store import SqliteStateStore, StateStore
from libcore.storage.file_store import FileStore, LocalFileStore
from libcore.storage.json_file_store import (
    JsonFileMemoryBackend,
    JsonFileSessionStore,
    JsonFileStateStore,
)

__all__ = [
    "AgentSessionRecord",
    "CheckpointRecord",
    "MemoryRecord",
    "MessageRecord",
    "SessionRecord",
    "MemoryBackend",
    "SessionStore",
    "StateStore",
    "FileStore",
    "SqliteDataStore",
    "SqliteMemoryStore",
    "SqliteStateStore",
    "LocalFileStore",
    "JsonFileSessionStore",
    "JsonFileStateStore",
    "JsonFileMemoryBackend",
]
