"""存储纯库：实体契约 + SQLite 数据/状态存储 + 本地文件存储。

对齐旧 ``app.runtime.kernel.storage_components``，但按 libcore 收敛为纯库模块
（标准库 sqlite3，零 SQLAlchemy 依赖），供 memory / context / knowledge 持久化使用。
"""
from libcore.storage.contracts import (
    AgentSessionRecord,
    CheckpointRecord,
    MessageRecord,
)
from libcore.storage.session_store import SessionStore
from libcore.storage.sqlite_store import SqliteDataStore
from libcore.storage.sqlite_memory_store import SqliteMemoryStore
from libcore.storage.state_store import SqliteStateStore
from libcore.storage.file_store import LocalFileStore

__all__ = [
    "AgentSessionRecord",
    "CheckpointRecord",
    "MessageRecord",
    "SessionStore",
    "SqliteDataStore",
    "SqliteMemoryStore",
    "SqliteStateStore",
    "LocalFileStore",
]
