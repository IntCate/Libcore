"""SQLite 状态存储（原生重写，不 import 旧 app.runtime）。

对齐旧 ``SqliteStateStore`` 语义，但用标准库 ``sqlite3``（零 SQLAlchemy 依赖）：
- 独立表 state_checkpoints（复合主键 id + scope）；
- checkpoint / graph_state 以 JSON 文本存储。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from libcore.storage.contracts import CheckpointRecord


class SqliteStateStore:
    """SQLite 内核状态存储（checkpoint / 长任务断点）。"""

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
        conn.execute(
            "CREATE TABLE IF NOT EXISTS state_checkpoints ("
            "id TEXT, scope TEXT, state TEXT, updated_at TEXT, metadata_json TEXT, "
            "PRIMARY KEY (id, scope))"
        )
        self._conn = conn
        return conn

    def save(self, record: CheckpointRecord) -> None:
        conn = self._ensure_conn()
        conn.execute(
            "INSERT OR REPLACE INTO state_checkpoints (id, scope, state, updated_at, metadata_json) "
            "VALUES (?,?,?,?,?)",
            (record.id, record.scope, json.dumps(record.state, ensure_ascii=False),
             record.updated_at or datetime.now().isoformat(),
             json.dumps(record.metadata, ensure_ascii=False) if record.metadata else None),
        )
        conn.commit()

    def get(self, id: str, scope: str) -> Optional[CheckpointRecord]:
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT * FROM state_checkpoints WHERE id=? AND scope=?", (id, scope)
        ).fetchone()
        if row is None:
            return None
        return CheckpointRecord(
            id=row["id"], scope=row["scope"],
            state=json.loads(row["state"] or "{}"),
            updated_at=row["updated_at"] or "",
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    def list(self, scope: str) -> List[CheckpointRecord]:
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT * FROM state_checkpoints WHERE scope=?", (scope,)
        ).fetchall()
        return [
            CheckpointRecord(
                id=r["id"], scope=r["scope"],
                state=json.loads(r["state"] or "{}"),
                updated_at=r["updated_at"] or "",
                metadata=json.loads(r["metadata_json"] or "{}"),
            )
            for r in rows
        ]

    def delete(self, id: str, scope: str) -> None:
        conn = self._ensure_conn()
        conn.execute("DELETE FROM state_checkpoints WHERE id=? AND scope=?", (id, scope))
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
