"""SQLite 记忆存储测试：importance 语义（0 必须保留，不得被改写为 0.5）。

bus.dispatch() 是异步的，用 asyncio.run() 包裹（不依赖 pytest-asyncio）。
"""
from __future__ import annotations

from libcore.storage.contracts import MemoryRecord
from libcore.storage.sqlite_memory_store import SqliteMemoryStore


class TestSqliteMemoryStoreImportance:
    def test_importance_zero_preserved(self):
        """importance=0 必须原样保留，不得被 _to_record 改写为 0.5。"""
        store = SqliteMemoryStore(":memory:")
        store.save(MemoryRecord(
            id="m1", session_id="s1", content="低优先级记忆",
            kind="fact", importance=0.0, tags=[], created_at=1.0,
        ))
        rows = store.list_all()
        assert len(rows) == 1
        assert rows[0].importance == 0.0

    def test_importance_roundtrip(self):
        """importance 各取值（0 / 0.5 / 1.0）读写一致。"""
        store = SqliteMemoryStore(":memory:")
        for i, imp in enumerate([0.0, 0.5, 1.0]):
            store.save(MemoryRecord(
                id=f"m{i}", session_id="s1", content=f"记忆{i}",
                kind="fact", importance=imp, tags=[], created_at=float(i),
            ))
        by_id = {r.id: r.importance for r in store.list_all()}
        assert by_id == {"m0": 0.0, "m1": 0.5, "m2": 1.0}
