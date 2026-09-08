"""存储层契约化验收：4 个存储域都有抽象接口，实现可替换（直接注入装配）。"""
import os
import tempfile

import pytest

from libcore.storage import (
    FileStore,
    LocalFileStore,
    SessionStore,
    SqliteDataStore,
    SqliteMemoryStore,
    SqliteStateStore,
    StateStore,
)


def test_session_store_is_contract():
    assert issubclass(SqliteDataStore, SessionStore)


def test_state_store_is_contract():
    assert issubclass(SqliteStateStore, StateStore)


def test_file_store_is_contract():
    assert issubclass(LocalFileStore, FileStore)


def test_file_store_contract_methods():
    """FileStore 契约：write/read/delete/stat 四方法，实现可替换。"""
    for m in ("write", "read", "delete", "stat"):
        assert hasattr(FileStore, m), f"FileStore 缺契约方法 {m}"


def test_local_file_store_implements_contract():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalFileStore(tmp)
        key = store.write("a/b.txt", b"hello")
        assert store.read(key) == b"hello"
        info = store.stat(key)
        assert info["size"] == 5
        store.delete(key)
        assert not os.path.exists(os.path.join(tmp, "a", "b.txt"))


def test_file_store_path_traversal_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalFileStore(tmp)
        with pytest.raises(ValueError):
            store.write("../escape.txt", b"x")


def test_memory_store_has_backend_contract():
    """记忆存储通过 MemoryBackend 契约注入（契约在存储层），SqliteMemoryStore 可被包装。"""
    from libcore.storage.memory_store import MemoryBackend
    from libcore.plugins.capabilities.memory import DefaultMemoryBackend

    assert issubclass(DefaultMemoryBackend, MemoryBackend)
    backend = DefaultMemoryBackend(store=SqliteMemoryStore(":memory:"))
    assert backend.store is not None
