"""CredentialPool 状态持久化层（sqlite，仅状态/计数，不落盘密钥）。

翻译自旧架构 ``llm_node_vendors/credential_pool_store.py``，原生重写：
    1. 使用标准库 sqlite3（零外部依赖），DB 文件默认 data/credential_pool.db，
       可用环境变量 LIBCORE_CREDENTIAL_POOL_DB 覆盖（测试 / 部署用）。
    2. **只落盘状态与计数，不落盘密钥**——credentials 仍由调用方提供，
       重启后由 PoolManager 重建 entry，再恢复状态。
    3. 每条记录：provider_slug + entry_id 为主键，保存 status / cooldown_until /
       success_count / failure_count / consecutive_5xx_count / dead_reason。
    4. 每操作独立连接（sqlite 自带连接级互斥 + timeout=5），可容忍轻量并发。

不变量守护：
    仅 import 标准库（os / sqlite3），零 app 依赖，零 pydantic。
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS credential_pool_state (
    provider_slug         TEXT NOT NULL,
    entry_id              TEXT NOT NULL,
    status                TEXT NOT NULL,
    cooldown_until        REAL NOT NULL DEFAULT 0,
    success_count         INTEGER NOT NULL DEFAULT 0,
    failure_count         INTEGER NOT NULL DEFAULT 0,
    consecutive_5xx_count INTEGER NOT NULL DEFAULT 0,
    dead_reason           TEXT DEFAULT '',
    PRIMARY KEY (provider_slug, entry_id)
)
"""


def _default_db_path() -> str:
    override = os.environ.get("LIBCORE_CREDENTIAL_POOL_DB")
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    # libcore/llm/ → libcore → 项目根（2 层 ..）
    backend_root = os.path.abspath(os.path.join(here, "..", ".."))
    return os.path.join(backend_root, "data", "credential_pool.db")


class CredentialPoolStore:
    """凭据池状态持久化（sqlite，每操作独立连接）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _default_db_path()
        dir_name = os.path.dirname(self.db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass  # WAL 不可用（只读介质等）时降级为默认 journal
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------

    def save_entry(self, provider_slug: str, entry: Any) -> None:
        """持久化单条 entry 状态（upsert；不保存密钥）。"""
        reason = ""
        labels = getattr(entry, "labels", None) or {}
        if isinstance(labels.get("dead_reason"), str):
            reason = labels["dead_reason"]
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO credential_pool_state
                    (provider_slug, entry_id, status, cooldown_until, success_count,
                     failure_count, consecutive_5xx_count, dead_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_slug, entry_id) DO UPDATE SET
                    status = excluded.status,
                    cooldown_until = excluded.cooldown_until,
                    success_count = excluded.success_count,
                    failure_count = excluded.failure_count,
                    consecutive_5xx_count = excluded.consecutive_5xx_count,
                    dead_reason = excluded.dead_reason
                """,
                (
                    provider_slug,
                    entry.id,
                    entry.status.value,
                    float(entry.cooldown_until or 0),
                    int(entry.success_count or 0),
                    int(entry.failure_count or 0),
                    int(entry.consecutive_5xx_count or 0),
                    reason,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_entry(self, provider_slug: str, entry_id: str) -> None:
        """删除单条记录（remove_entry 时）。"""
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM credential_pool_state WHERE provider_slug=? AND entry_id=?",
                (provider_slug, entry_id),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_pool(self, provider_slug: str) -> None:
        """删除某 provider 的全部记录（unregister 时）。"""
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM credential_pool_state WHERE provider_slug=?",
                (provider_slug,),
            )
            conn.commit()
        finally:
            conn.close()

    def load_entries(self, provider_slug: str) -> List[Dict[str, Any]]:
        """读取某 provider 的全部持久化状态。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT entry_id, status, cooldown_until, success_count, failure_count,
                       consecutive_5xx_count, dead_reason
                FROM credential_pool_state
                WHERE provider_slug = ?
                """,
                (provider_slug,),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "entry_id": row[0],
                "status": row[1],
                "cooldown_until": row[2],
                "success_count": row[3],
                "failure_count": row[4],
                "consecutive_5xx_count": row[5],
                "dead_reason": row[6],
            }
            for row in rows
        ]
