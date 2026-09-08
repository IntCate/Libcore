"""JSON 文件存储：会话/状态/记忆三个存储域的 JSON 文件实现。

对齐"组件只依赖契约、不依赖实现"：三个实现分别实现 SessionStore / StateStore /
MemoryBackend 契约，用 JSON 文件持久化，方便代码测试与人工检视（无需 SQLite）。
每个会话/检查点一个 .json 文件；记忆为单文件列表。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

from libcore.storage.contracts import AgentSessionRecord, CheckpointRecord, MemoryRecord, MessageRecord
from libcore.storage.memory_store import MemoryBackend, _keywords
from libcore.storage.session_store import SessionStore
from libcore.storage.state_store import StateStore


def _safe_join(base_dir: str, name: str) -> str:
    """把 name 安全拼到 base_dir 下，拒绝路径穿越。"""
    base = os.path.realpath(base_dir)
    candidate = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([base, candidate]) != base:
        raise ValueError(f"JSON 文件存储拒绝越界路径: {name}")
    return candidate


def _to_dict(obj: Any) -> Dict[str, Any]:
    """dataclass → dict（递归处理内嵌 dataclass 与列表）。"""
    if is_dataclass(obj):
        return {k: _to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj


def _write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class JsonFileSessionStore(SessionStore):
    """会话存储的 JSON 文件实现：每个会话一个 ``<session_id>.json``。"""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = os.path.realpath(base_dir)
        os.makedirs(self._base_dir, exist_ok=True)

    def _path(self, session_id: str) -> str:
        return _safe_join(self._base_dir, f"{session_id}.json")

    def get_agent_session(self, session_id: str) -> Optional[AgentSessionRecord]:
        raw = _read_json(self._path(session_id))
        if raw is None:
            return None
        raw["messages"] = [MessageRecord(**m) for m in raw.get("messages", [])]
        return AgentSessionRecord(**raw)

    def save_agent_session(self, session: AgentSessionRecord) -> None:
        _write_json(self._path(session.id), _to_dict(session))

    def list_agent_sessions(self, chat_id: str) -> List[AgentSessionRecord]:
        result: List[AgentSessionRecord] = []
        for name in os.listdir(self._base_dir):
            if not name.endswith(".json"):
                continue
            raw = _read_json(os.path.join(self._base_dir, name))
            if raw and raw.get("chat_id") == chat_id:
                result.append(AgentSessionRecord(
                    id=raw["id"], chat_id=raw["chat_id"],
                    created_at=raw.get("created_at", ""),
                    updated_at=raw.get("updated_at", ""),
                    graph_state=raw.get("graph_state"),
                    current_node=raw.get("current_node", ""),
                    step_count=raw.get("step_count", 0),
                    metadata=raw.get("metadata", {}),
                ))
        return result

    def delete_agent_session(self, session_id: str) -> None:
        path = self._path(session_id)
        if os.path.exists(path):
            os.remove(path)


class JsonFileStateStore(StateStore):
    """状态存储的 JSON 文件实现：每个检查点一个 ``<scope>__<id>.json``。"""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = os.path.realpath(base_dir)
        os.makedirs(self._base_dir, exist_ok=True)

    def _path(self, id: str, scope: str) -> str:
        return _safe_join(self._base_dir, f"{scope}__{id}.json")

    def save(self, record: CheckpointRecord) -> None:
        _write_json(self._path(record.id, record.scope), _to_dict(record))

    def get(self, id: str, scope: str) -> Optional[CheckpointRecord]:
        raw = _read_json(self._path(id, scope))
        return CheckpointRecord(**raw) if raw else None

    def list(self, scope: str) -> List[CheckpointRecord]:
        result: List[CheckpointRecord] = []
        prefix = f"{scope}__"
        for name in os.listdir(self._base_dir):
            if not name.endswith(".json") or not name.startswith(prefix):
                continue
            raw = _read_json(os.path.join(self._base_dir, name))
            if raw:
                result.append(CheckpointRecord(**raw))
        return result

    def delete(self, id: str, scope: str) -> None:
        path = self._path(id, scope)
        if os.path.exists(path):
            os.remove(path)


class JsonFileMemoryBackend(MemoryBackend):
    """记忆后端的 JSON 文件实现：单文件 ``memories.json`` 存全部记忆记录。

    复刻 DefaultMemoryBackend 的检索语义（关键词命中 + 会话过滤 + importance 排序），
    但持久化到 JSON 文件，跨实例/跨进程有效。
    """

    def __init__(self, base_dir: str) -> None:
        self._base_dir = os.path.realpath(base_dir)
        os.makedirs(self._base_dir, exist_ok=True)
        self._path = os.path.join(self._base_dir, "memories.json")

    def _load(self) -> List[MemoryRecord]:
        raw = _read_json(self._path)
        return [MemoryRecord(**r) for r in raw] if raw else []

    def _save(self, records: List[MemoryRecord]) -> None:
        _write_json(self._path, [_to_dict(r) for r in records])

    def remember(self, content: str, *, session_id: str = "", kind: str = "fact",
                 importance: float = 0.5, tags: Optional[List[str]] = None) -> MemoryRecord:
        rec = MemoryRecord(
            id=f"mem_{uuid.uuid4().hex[:16]}", session_id=session_id, content=content,
            kind=kind, importance=importance, tags=list(tags or []),
            created_at=time.time(),
        )
        records = self._load()
        records.append(rec)
        self._save(records)
        return rec

    def list_all(self) -> List[MemoryRecord]:
        return self._load()

    def retrieve(self, query: str, *, session_id: str, limit: int = 5) -> List[str]:
        kws = _keywords(query)
        scored: List[tuple] = []
        for rec in self._load():
            if rec.session_id and rec.session_id != session_id:
                continue
            hay = f"{rec.content} {' '.join(rec.tags)}".lower()
            hits = sum(1 for kw in kws if kw and kw in hay)
            if hits:
                scored.append((hits, rec.importance, rec))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [r.content for _h, _i, r in scored[:limit]]
