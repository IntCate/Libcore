"""状态存储契约 + SQLite 实现（原生重写，不 import 旧 app.runtime）。

对齐"组件只依赖契约、不依赖实现"原则：状态存储（checkpoint / 长任务断点）
通过 ``StateStore`` 抽象契约解耦，实现方（SQLite / 文件 / 内存 / 远程）可任意替换。

方案 B：状态独立成表（state_checkpoints），同一 scope 可存多个检查点（多版本），
支持回滚到任意历史版本。checkpoint / graph_state 以 JSON 文本存储。
"""
from __future__ import annotations

import json
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from libcore.storage.contracts import CheckpointRecord


class StateStore(ABC):
    """状态存储契约：内核状态检查点（CheckpointRecord）的读写。

    状态独立于会话（方案 B）：同一 scope 可存多个检查点（多版本），
    支持回滚到任意历史版本。实现方负责把 dataclass 转成自己的存储格式
    （SQL 行 / JSON 文件 / 远程 API），并转回 dataclass。
    """

    @abstractmethod
    def save(self, record: CheckpointRecord) -> None:
        """保存一个检查点（同 id+scope 覆盖，不同 id 追加为历史版本）。"""

    @abstractmethod
    def get(self, id: str, scope: str) -> Optional[CheckpointRecord]:
        """按 id+scope 读回检查点；不存在返回 None。"""

    @abstractmethod
    def list(self, scope: str) -> List[CheckpointRecord]:
        """按 scope 列出全部检查点（多版本历史）。"""

    @abstractmethod
    def delete(self, id: str, scope: str) -> None:
        """删除单个检查点。"""


class SqliteStateStore(StateStore):
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
