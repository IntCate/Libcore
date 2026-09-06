"""knowledge 门面测试：find / read / search / ingest / stats / clear / 降级。

bus.dispatch() 是异步的，用 asyncio.run() 包裹（不依赖 pytest-asyncio）。
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from libcore.kernel.bus import Dispatch
from libcore.plugins.capabilities import knowledge as kb_cap


def _call(bus, d):
    return asyncio.run(bus.dispatch(d))


def _dispatch(bus, op, payload=None):
    return Dispatch(target="knowledge", op=op, payload=payload or {})


def _make_kb_root(tmp_path: Path) -> Path:
    """构造一个含单个知识库的临时目录（kb.yaml + 一个文档）。"""
    kb = tmp_path / "docs"
    kb.mkdir()
    (kb / "kb.yaml").write_text(
        "name: docs\ndescription: 测试知识库\n", encoding="utf-8"
    )
    (kb / "intro.md").write_text(
        "libcore 的能力插件体系：门面、输入节点、系统能力。", encoding="utf-8"
    )
    return tmp_path


class TestKnowledgeCapability:
    def test_find_lists_knowledge_bases(self, bus, tmp_path):
        kb_cap.register(bus, store=kb_cap.InMemoryKnowledgeStore())
        # 用临时目录替换默认知识库目录
        root = _make_kb_root(tmp_path)
        engine = kb_cap.KnowledgeEngine(kb_root=root)
        result = engine.find()
        assert result.ok
        assert result.data["count"] == 1
        assert result.data["knowledge_bases"][0]["name"] == "docs"

    def test_find_keyword_filter(self, bus, tmp_path):
        root = _make_kb_root(tmp_path)
        engine = kb_cap.KnowledgeEngine(kb_root=root)
        result = engine.find(keyword="不存在")
        assert result.ok
        assert result.data["count"] == 0

    def test_read_returns_meta_and_files(self, bus, tmp_path):
        root = _make_kb_root(tmp_path)
        engine = kb_cap.KnowledgeEngine(kb_root=root)
        result = engine.read("docs")
        assert result.ok
        assert result.data["name"] == "docs"
        assert "intro.md" in result.data["files"]

    def test_read_unknown_kb_fails(self, bus, tmp_path):
        root = _make_kb_root(tmp_path)
        engine = kb_cap.KnowledgeEngine(kb_root=root)
        result = engine.read("nope")
        assert not result.ok
        assert "不存在" in result.error

    def test_ingest_then_search(self, bus, tmp_path):
        root = _make_kb_root(tmp_path)
        engine = kb_cap.KnowledgeEngine(kb_root=root)
        doc = root / "docs" / "intro.md"
        ing = engine.ingest("docs", str(doc))
        assert ing.ok
        assert ing.data["ingested"] is True
        assert ing.data["chunk_count"] >= 1

        res = engine.search("docs", "能力插件")
        assert res.ok
        assert res.data["hits"]
        assert "能力插件" in res.data["hits"][0]["text"]

    def test_search_unknown_kb_fails(self, bus, tmp_path):
        root = _make_kb_root(tmp_path)
        engine = kb_cap.KnowledgeEngine(kb_root=root)
        result = engine.search("nope", "查询")
        assert not result.ok

    def test_stats_and_clear(self, bus, tmp_path):
        root = _make_kb_root(tmp_path)
        engine = kb_cap.KnowledgeEngine(kb_root=root)
        doc = root / "docs" / "intro.md"
        engine.ingest("docs", str(doc))
        st = engine.stats("docs")
        assert st.ok
        assert st.data["count"] >= 1

        cl = engine.clear("docs")
        assert cl.ok
        assert cl.data["cleared"] is True
        st2 = engine.stats("docs")
        assert st2.data["count"] == 0

    def test_register_dispatch_ops(self, bus, tmp_path):
        kb_cap.register(bus, store=kb_cap.InMemoryKnowledgeStore())
        # 用临时目录替换默认知识库目录
        root = _make_kb_root(tmp_path)
        engine = kb_cap.KnowledgeEngine(kb_root=root)
        # 通过总线 dispatch find
        result = _call(bus, _dispatch(bus, "find"))
        assert result.ok
        # 未知 op 报错
        bad = _call(bus, _dispatch(bus, "bogus"))
        assert not bad.ok
        assert "未知操作" in bad.error
