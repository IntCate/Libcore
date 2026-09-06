"""SQLite 记忆存储（跨会话持久，原生 sqlite3，零外部依赖）。

对齐 ``InMemoryMemoryStore`` 的检索语义（关键词 + 会话过滤 + importance 排序），
但持久化到 SQLite，保证跨进程/跨会话记忆不丢失。返回 / 接收纯 dataclass
（``MemoryRecord``），零暴露 ORM 对象。

装配（零耦合，可拆卸）：
- ``SqliteMemoryStore(db_path)`` 独立建表（memory 表），不依赖会话存储；
- 可注入 ``DefaultMemoryBackend(store=SqliteMemoryStore(...))`` 替换默认内存后端；
- 检索/存储异常由上层捕获降级为空记忆（无感原则）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from libcore.plugins.capabilities.memory import MemoryRecord, _keywords


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Optional[str], default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class SqliteMemoryStore:
    """SQLite 记忆存储：save / retrieve（关键词 + 会话过滤 + importance 排序）。"""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if self._db_path != ":memory:":
            parent = os.path.dirname(self._db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, session_id TEXT, content TEXT, kind TEXT,
                importance REAL, tags TEXT, created_at REAL
            );
            """
        )
        self._conn = conn
        return conn

    def save(self, record: MemoryRecord) -> None:
        conn = self._ensure_conn()
        conn.execute(
            "INSERT OR REPLACE INTO memories "
            "(id, session_id, content, kind, importance, tags, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (record.id, record.session_id, record.content, record.kind,
             record.importance, _dumps(record.tags), record.created_at),
        )
        conn.commit()

    def retrieve(self, query: str, *, session_id: str, limit: int = 5) -> List[MemoryRecord]:
        conn = self._ensure_conn()
        kws = _keywords(query)
        rows = conn.execute("SELECT * FROM memories").fetchall()
        scored: List[tuple] = []
        for row in rows:
            rec = self._to_record(row)
            if rec.session_id and rec.session_id != session_id:
                continue  # 只取本会话（空=全局）记忆
            hay = f"{rec.content} {' '.join(rec.tags)}".lower()
            hits = sum(1 for kw in kws if kw and kw in hay)
            if hits:
                scored.append((hits, rec.importance, rec))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [r for _h, _i, r in scored[:limit]]

    def list_all(self) -> List[MemoryRecord]:
        conn = self._ensure_conn()
        rows = conn.execute("SELECT * FROM memories ORDER BY created_at DESC").fetchall()
        return [self._to_record(r) for r in rows]

    def delete(self, record_id: str) -> None:
        conn = self._ensure_conn()
        conn.execute("DELETE FROM memories WHERE id=?", (record_id,))
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _to_record(row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"], session_id=row["session_id"] or "", content=row["content"] or "",
            kind=row["kind"] or "fact", importance=row["importance"] or 0.5,
            tags=_loads(row["tags"], []), created_at=row["created_at"] or 0.0,
        )
