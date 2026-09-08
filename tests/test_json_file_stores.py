"""JSON 文件存储测试：会话/状态/记忆三个存储域的文件实现（方便代码测试与人工检视）。

对齐"组件只依赖契约、不依赖实现"：三个 JSON 实现分别实现 SessionStore / StateStore /
MemoryBackend 契约，可注入给 session / context / memory 插件，替代 SQLite 或内存实现。
"""
from __future__ import annotations

import json

import pytest

from libcore.storage.contracts import AgentSessionRecord, CheckpointRecord, MemoryRecord, MessageRecord
from libcore.storage.memory_store import MemoryBackend
from libcore.storage.session_store import SessionStore
from libcore.storage.state_store import StateStore
from libcore.storage.json_file_store import (
    JsonFileMemoryBackend,
    JsonFileSessionStore,
    JsonFileStateStore,
)


# ── 会话存储 ────────────────────────────────────────────────────────────

def test_json_session_store_implements_contract():
    assert issubclass(JsonFileSessionStore, SessionStore)


def test_json_session_store_roundtrip(tmp_path):
    store = JsonFileSessionStore(str(tmp_path))
    session = AgentSessionRecord(
        id="s1", chat_id="c1", created_at="t0", updated_at="t1",
        graph_state={"step": 2}, current_node="n2", step_count=2,
        messages=[MessageRecord(id="m1", chat_id="c1", role="user", content="你好")],
        metadata={"k": "v"},
    )
    store.save_agent_session(session)
    got = store.get_agent_session("s1")
    assert got is not None
    assert got.id == "s1"
    assert got.graph_state == {"step": 2}
    assert got.metadata == {"k": "v"}
    assert len(got.messages) == 1
    assert got.messages[0].content == "你好"


def test_json_session_store_list_and_delete(tmp_path):
    store = JsonFileSessionStore(str(tmp_path))
    store.save_agent_session(AgentSessionRecord(id="a", chat_id="c1"))
    store.save_agent_session(AgentSessionRecord(id="b", chat_id="c1"))
    store.save_agent_session(AgentSessionRecord(id="c", chat_id="c2"))
    assert {s.id for s in store.list_agent_sessions("c1")} == {"a", "b"}
    store.delete_agent_session("a")
    assert store.get_agent_session("a") is None


def test_json_session_store_missing_returns_none(tmp_path):
    store = JsonFileSessionStore(str(tmp_path))
    assert store.get_agent_session("nope") is None


# ── 状态存储 ───────────────────────────────────────────────────────────

def test_json_state_store_implements_contract():
    assert issubclass(JsonFileStateStore, StateStore)


def test_json_state_store_multi_version_rollback(tmp_path):
    store = JsonFileStateStore(str(tmp_path))
    store.save(CheckpointRecord(id="v1", scope="task-1", state={"step": 1}))
    store.save(CheckpointRecord(id="v2", scope="task-1", state={"step": 2}))
    store.save(CheckpointRecord(id="v3", scope="task-1", state={"step": 3}))
    assert len(store.list("task-1")) == 3
    assert store.get("v1", "task-1").state == {"step": 1}
    assert store.get("v3", "task-1").state == {"step": 3}


def test_json_state_store_scope_isolation_and_delete(tmp_path):
    store = JsonFileStateStore(str(tmp_path))
    store.save(CheckpointRecord(id="v1", scope="task-a", state={"x": 1}))
    store.save(CheckpointRecord(id="v1", scope="task-b", state={"x": 2}))
    assert store.get("v1", "task-a").state == {"x": 1}
    assert store.get("v1", "task-b").state == {"x": 2}
    store.delete("v1", "task-a")
    assert store.get("v1", "task-a") is None
    assert store.get("v1", "task-b") is not None


# ── 记忆存储 ────────────────────────────────────────────────────────────

def test_json_memory_backend_implements_contract():
    assert issubclass(JsonFileMemoryBackend, MemoryBackend)


def test_json_memory_backend_remember_and_retrieve(tmp_path):
    backend = JsonFileMemoryBackend(str(tmp_path))
    backend.remember("用户喜欢喝美式咖啡", session_id="s1", kind="fact", importance=0.9)
    hits = backend.retrieve("美式咖啡", session_id="s1", limit=5)
    assert "美式咖啡" in hits[0]


def test_json_memory_backend_importance_preserved(tmp_path):
    backend = JsonFileMemoryBackend(str(tmp_path))
    backend.remember("低优先级记忆", session_id="s1", importance=0.0)
    recs = backend.list_all()
    assert len(recs) == 1
    assert recs[0].importance == 0.0


def test_json_memory_backend_persists_across_instances(tmp_path):
    backend = JsonFileMemoryBackend(str(tmp_path))
    backend.remember("跨实例记忆", session_id="s1", importance=0.5)
    reloaded = JsonFileMemoryBackend(str(tmp_path))
    hits = reloaded.retrieve("跨实例", session_id="s1", limit=5)
    assert hits and "跨实例" in hits[0]


# ── 文件可读性（人工检视）──────────────────────────────────────────────

def test_json_files_are_human_readable(tmp_path):
    store = JsonFileSessionStore(str(tmp_path))
    store.save_agent_session(AgentSessionRecord(id="s1", chat_id="c1", current_node="n1"))
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    raw = json.loads(files[0].read_text(encoding="utf-8"))
    assert raw["id"] == "s1"
    assert raw["current_node"] == "n1"
