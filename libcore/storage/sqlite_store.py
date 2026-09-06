"""SQLite 数据存储（原生重写，不 import 旧 app.runtime）。

对齐旧 ``SqliteDataStore`` 语义，但用标准库 ``sqlite3``（零 SQLAlchemy 依赖）：
- chat / message / agent_session 三张表；
- 懒加载建表（首次访问才创建连接 + 建表）；
- 返回 / 接收 contracts.py 纯 dataclass，零暴露 ORM 对象。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from libcore.storage.contracts import (
    AgentSessionRecord,
    MessageRecord,
)
from libcore.storage.session_store import SessionStore


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Optional[str], default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class SqliteDataStore(SessionStore):
    """SQLite 关系型数据存储（chat/message/agent_session），实现 SessionStore 契约。"""

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
        self._create_tables(conn)
        self._conn = conn
        return conn

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY, title TEXT, preview TEXT,
                created_at TEXT, updated_at TEXT, pinned INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, chat_id TEXT, role TEXT, message_type TEXT,
                content TEXT, reasoning_content TEXT, created_at TEXT, model TEXT,
                files TEXT, agent_session_id TEXT, agent_node TEXT, agent_step INTEGER,
                agent_metadata TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id TEXT PRIMARY KEY, chat_id TEXT, created_at TEXT, updated_at TEXT,
                graph_state TEXT, current_node TEXT, step_count INTEGER
            );
            """
        )

    # ── 消息 ──

    def list_messages(self, session_id: str) -> List[MessageRecord]:
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT * FROM messages WHERE chat_id=? ORDER BY created_at ASC", (session_id,)
        ).fetchall()
        return [self._to_message(r) for r in rows]

    def save_messages(self, session_id: str, messages: List[MessageRecord]) -> None:
        conn = self._ensure_conn()
        for msg in messages:
            conn.execute(
                "INSERT OR REPLACE INTO messages "
                "(id, chat_id, role, message_type, content, reasoning_content, created_at, model, "
                "files, agent_session_id, agent_node, agent_step, agent_metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (msg.id, session_id, msg.role, msg.message_type, msg.content,
                 msg.reasoning_content, msg.created_at or datetime.now().isoformat(),
                 msg.model, _dumps(msg.files), msg.agent_session_id, msg.agent_node,
                 msg.agent_step, _dumps(msg.agent_metadata) if msg.agent_metadata is not None else None),
            )
        conn.commit()

    # ── 智能体会话 ──

    def get_agent_session(self, session_id: str) -> Optional[AgentSessionRecord]:
        conn = self._ensure_conn()
        row = conn.execute("SELECT * FROM agent_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            return None
        session = self._to_agent_session(row)
        session.messages = self.list_messages(session_id)
        return session

    def save_agent_session(self, session: AgentSessionRecord) -> None:
        conn = self._ensure_conn()
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO agent_sessions "
            "(id, chat_id, created_at, updated_at, graph_state, current_node, step_count) "
            "VALUES (?,?,?,?,?,?,?)",
            (session.id, session.chat_id, session.created_at or now, session.updated_at or now,
             _dumps(session.graph_state) if session.graph_state is not None else None,
             session.current_node, session.step_count),
        )
        conn.commit()
        if session.messages:
            self.save_messages(session.id, session.messages)

    def list_agent_sessions(self, chat_id: str) -> List[AgentSessionRecord]:
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT * FROM agent_sessions WHERE chat_id=?", (chat_id,)
        ).fetchall()
        return [self._to_agent_session(r) for r in rows]

    def delete_agent_session(self, session_id: str) -> None:
        conn = self._ensure_conn()
        conn.execute("DELETE FROM messages WHERE chat_id=?", (session_id,))
        conn.execute("DELETE FROM agent_sessions WHERE id=?", (session_id,))
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── 内部转换 ──

    @staticmethod
    def _to_message(row) -> MessageRecord:
        return MessageRecord(
            id=row["id"], chat_id=row["chat_id"], role=row["role"],
            message_type=row["message_type"] or "normal", content=row["content"] or "",
            reasoning_content=row["reasoning_content"],
            created_at=row["created_at"] or "", model=row["model"],
            files=_loads(row["files"], []), agent_session_id=row["agent_session_id"],
            agent_node=row["agent_node"], agent_step=row["agent_step"],
            agent_metadata=_loads(row["agent_metadata"], None),
        )

    @staticmethod
    def _to_agent_session(row) -> AgentSessionRecord:
        return AgentSessionRecord(
            id=row["id"], chat_id=row["chat_id"],
            created_at=row["created_at"] or "", updated_at=row["updated_at"] or "",
            graph_state=_loads(row["graph_state"], None),
            current_node=row["current_node"] or "", step_count=row["step_count"] or 0,
        )
