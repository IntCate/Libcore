"""状态存储契约化测试（方案 B：状态独立成表 + 多版本回滚 + 可替换实现）。"""
import pytest

from libcore.storage.contracts import CheckpointRecord
from libcore.storage.state_store import StateStore, SqliteStateStore


def test_state_store_is_abstract_contract():
    """StateStore 是抽象契约，不能直接实例化。"""
    with pytest.raises(TypeError):
        StateStore()


def test_sqlite_state_store_implements_contract():
    """SqliteStateStore 实现 StateStore 契约。"""
    assert issubclass(SqliteStateStore, StateStore)


def test_state_store_multi_version_rollback():
    """方案 B：同一 scope 可存多个检查点（多版本），可回滚到任意历史版本。"""
    store = SqliteStateStore(":memory:")
    store.save(CheckpointRecord(id="v1", scope="task-1", state={"step": 1}))
    store.save(CheckpointRecord(id="v2", scope="task-1", state={"step": 2}))
    store.save(CheckpointRecord(id="v3", scope="task-1", state={"step": 3}))

    versions = store.list("task-1")
    assert len(versions) == 3

    v1 = store.get("v1", "task-1")
    assert v1 is not None and v1.state == {"step": 1}
    v3 = store.get("v3", "task-1")
    assert v3 is not None and v3.state == {"step": 3}


def test_state_store_scope_isolation():
    """不同 scope 的检查点互不污染。"""
    store = SqliteStateStore(":memory:")
    store.save(CheckpointRecord(id="v1", scope="task-a", state={"x": 1}))
    store.save(CheckpointRecord(id="v1", scope="task-b", state={"x": 2}))

    assert store.get("v1", "task-a").state == {"x": 1}
    assert store.get("v1", "task-b").state == {"x": 2}


def test_state_store_delete():
    """删除单个检查点。"""
    store = SqliteStateStore(":memory:")
    store.save(CheckpointRecord(id="v1", scope="task-1", state={"step": 1}))
    store.delete("v1", "task-1")
    assert store.get("v1", "task-1") is None
