"""SQLite 数据存储测试：metadata 持久化（消息与会话的 metadata 必须完整读写）。

bus.dispatch() 是异步的，用 asyncio.run() 包裹（不依赖 pytest-asyncio）。
"""
from __future__ import annotations

from libcore.storage.contracts import MessageRecord, AgentSessionRecord
from libcore.storage.sqlite_store import SqliteDataStore


class TestSqliteStoreMetadata:
    def test_message_metadata_persisted(self):
        """MessageRecord.metadata 必须完整持久化并读回。"""
        store = SqliteDataStore(":memory:")
        msg = MessageRecord(
            id="msg1", chat_id="chat1", role="user", content="hi",
            metadata={"source": "test", "priority": 3},
        )
        store.save_messages("chat1", [msg])
        loaded = store.list_messages("chat1")
        assert len(loaded) == 1
        assert loaded[0].metadata == {"source": "test", "priority": 3}

    def test_agent_session_metadata_persisted(self):
        """AgentSessionRecord.metadata 必须完整持久化并读回。"""
        store = SqliteDataStore(":memory:")
        session = AgentSessionRecord(
            id="sess1", chat_id="chat1",
            metadata={"owner": "alice", "tags": ["a", "b"]},
        )
        store.save_agent_session(session)
        loaded = store.get_agent_session("sess1")
        assert loaded is not None
        assert loaded.metadata == {"owner": "alice", "tags": ["a", "b"]}
