"""内置能力插件：knowledge —— 知识库门面（唯一总线节点，渐进披露知识库）。

与 ``skill`` / ``tools`` / ``mcp`` 同为一级"真入口"。manifest 只暴露 ``knowledge``
一个知识库能力，**具体知识库的概要不常驻清单**——Agent 需要时才逐层索取
（渐进披露，省 token）：

- L1  ``knowledge`` find    → 知识库索引（name + 一句 description），供选择
- L2  ``knowledge`` read    → 某知识库的元信息（文档数 / 来源 / 可检索字段）
- L3  ``knowledge`` search  → 在某知识库内按查询召回相关片段
- L4  ``knowledge`` ingest  → 加载文件 → 切分 → 入库（可选嵌入）
- L5  ``knowledge`` stats / clear → 统计 / 清空某知识库

门面哲学：agent 只看到 ``knowledge`` 这一层薄门面，内部检索是开发者黑盒
（默认零依赖内存实现，可注入自定义后端）。内部怎么检索（关键词 / 向量 / 混合）
完全由开发者决定，框架只约定数据格式；agent 永远只消费语义结果。

具体知识库是**目录资源**（``libcore/knowledge_bases/`` 下每个子目录 = 一个知识库，
含文档文件 + 可选 ``kb.yaml`` 元信息），与 ``libcore/skills/`` 平级的**非总线节点**
资源库，不 import、不注册，门面启动时直接文件扫描聚合。存储后端可注入
（默认内存实现，可包装真实向量库）。执行信号收敛于 ``knowledge`` 的 ingest/search op，
aspect 护栏只签此处。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from libcore.kernel.bus import Dispatch, CapabilityResult
from ..resources.knowledge.documents import KBDocument

DESCRIPTION = (
    "知识库门面（manifest 唯一知识库能力）：点名我即可。find 列出知识库索引"
    "（name+一句描述，不含内容），再用 payload['name'] 指定某知识库：read 取元信息、"
    "search 按查询召回相关片段、ingest 加载文件入库、stats 统计、clear 清空。"
    "知识库概要不常驻清单，需要时点我索取。具体知识库是 knowledge_bases/ 目录资源。"
)


def _default_kb_root() -> Path:
    """默认知识库数据目录：仓库根目录 data/knowledge_bases/。"""
    # 本文件位于 libcore/plugins/capabilities/，向上四级到仓库根，再进 data/
    return Path(__file__).resolve().parent.parent.parent.parent / "data" / "knowledge_bases"


def _keywords(query: str) -> List[str]:
    """把查询拆成关键词：英文按词，中文按二元切分（对齐 memory）。"""
    q = (query or "").lower()
    kws = re.findall(r"[a-z0-9]+", q)
    for chunk in re.findall(r"[\u4e00-\u9fff]+", q):
        if len(chunk) == 1:
            kws.append(chunk)
        else:
            kws.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return [w for w in kws if w]


# ── 知识库存储契约（零依赖，轻量）─────────────────────────────────────

class KnowledgeStore:
    """知识库存储后端接口。

    开发者可用自定义后端（如包装真实向量库 / 混合检索 / rerank）替换默认内存实现。
    方法异常由上层捕获降级为空结果。
    """
    def upsert(self, kb: str, docs: List[KBDocument]) -> Dict[str, Any]:
        raise NotImplementedError

    def search(self, kb: str, query: str, *, top_k: int = 5,
               score_threshold: float = 0.0) -> List[KBDocument]:
        raise NotImplementedError

    def stats(self, kb: str) -> Dict[str, Any]:
        raise NotImplementedError

    def clear(self, kb: str) -> bool:
        raise NotImplementedError


class InMemoryKnowledgeStore(KnowledgeStore):
    """默认内存知识库存储：关键词召回 + 命中率相似度排序（零外部依赖）。

    每个知识库独立分桶（按 ``kb`` 作用域隔离），按 query 拆词后在桶内召回并打分，
    命中关键词越多得分越高，按得分降序取 top_k。保证"零配置可裸跑、有检索可用"。
    """

    def __init__(self) -> None:
        self._docs: Dict[str, Dict[str, KBDocument]] = {}

    def upsert(self, kb: str, docs: List[KBDocument]) -> Dict[str, Any]:
        bucket = self._docs.setdefault(kb, {})
        for d in docs:
            d.metadata = dict(d.metadata or {})
            d.metadata.setdefault("kb", kb)
            bucket[d.metadata.get("id") or f"kb_{uuid.uuid4().hex[:16]}"] = d
        return {"success": True, "count": len(docs), "kb": kb}

    def search(self, kb: str, query: str, *, top_k: int = 5,
               score_threshold: float = 0.0) -> List[KBDocument]:
        bucket = self._docs.get(kb, {})
        kws = _keywords(query)
        scored: List[KBDocument] = []
        for d in bucket.values():
            hay = f"{d.text} {' '.join(str(v) for v in d.metadata.values())}".lower()
            hits = sum(1 for kw in kws if kw and kw in hay)
            if hits:
                d.score = hits / max(1, len(kws))
                scored.append(d)
        scored.sort(key=lambda d: d.score, reverse=True)
        return [d for d in scored if d.score >= score_threshold][:top_k]

    def stats(self, kb: str) -> Dict[str, Any]:
        return {"backend": "memory", "kb": kb, "count": len(self._docs.get(kb, {}))}

    def clear(self, kb: str) -> bool:
        self._docs.pop(kb, None)
        return True


# ── 知识库引擎（与总线零耦合，仅读写资源 + 存储）──────────────────────

class KnowledgeEngine:
    """知识库门面引擎。知识库目录 + 存储后端可注入（默认 libcore/knowledge_bases）。

    与总线零耦合：不 dispatch 任何节点，仅按 kb 解析目录资源并委托 store 检索。
    """

    def __init__(self, kb_root: Optional[Path] = None,
                 store: Optional[KnowledgeStore] = None) -> None:
        self._root = (kb_root or _default_kb_root()).resolve()
        self._store = store or InMemoryKnowledgeStore()

    # ---- find：知识库索引 ----

    def find(self, keyword: str = "") -> CapabilityResult:
        if not self._root.is_dir():
            return CapabilityResult(ok=False, error=f"知识库目录不存在：{self._root}")
        kw = keyword.lower()
        index: List[dict] = []
        for kb_dir in sorted(p for p in self._root.iterdir() if p.is_dir()):
            meta, _ = self._read_meta(kb_dir)
            name = meta.get("name") or kb_dir.name
            description = meta.get("description") or ""
            if kw and kw not in name.lower() and kw not in description.lower():
                continue
            index.append({
                "name": name,
                "description": description,
                "path": str(kb_dir.relative_to(self._root)),
            })
        return CapabilityResult(ok=True, data={"knowledge_bases": index, "count": len(index)})

    # ---- read：知识库元信息 ----

    def read(self, name: str) -> CapabilityResult:
        kb_dir = self._resolve(name)
        if kb_dir is None:
            return CapabilityResult(ok=False, error=f"知识库不存在：{name!r}")
        meta, _ = self._read_meta(kb_dir)
        files = sorted(
            str(p.relative_to(kb_dir))
            for p in kb_dir.rglob("*")
            if p.is_file() and p.name != "kb.yaml" and "__pycache__" not in str(p)
        )
        return CapabilityResult(ok=True, data={
            "name": meta.get("name") or kb_dir.name,
            "metadata": {k: v for k, v in meta.items() if k != "name"},
            "files": files,
            "stats": self._store.stats(kb_dir.name),
        })

    # ---- search：召回 ----

    def search(self, name: str, query: str, *, top_k: int = 5,
               score_threshold: float = 0.0) -> CapabilityResult:
        kb_dir = self._resolve(name)
        if kb_dir is None:
            return CapabilityResult(ok=False, error=f"知识库不存在：{name!r}")
        try:
            hits = self._store.search(
                kb_dir.name, query, top_k=top_k, score_threshold=score_threshold,
            )
        except Exception as e:
            return CapabilityResult(ok=False, data={}, error=f"检索失败：{e}")
        return CapabilityResult(ok=True, data={
            "hits": [{"text": h.text, "score": h.score,
                      "metadata": h.metadata} for h in hits],
        })

    # ---- ingest：加载文件 → 切分 → 入库 ----

    def ingest(self, name: str, path: str, *, chunk_size: int = 1000,
               chunk_overlap: int = 200) -> CapabilityResult:
        kb_dir = self._resolve(name)
        if kb_dir is None:
            return CapabilityResult(ok=False, error=f"知识库不存在：{name!r}")
        from ..resources.knowledge.loaders import load_document
        from ..resources.knowledge.splitter import RecursiveSplitter

        docs = load_document(path)
        if not docs:
            return CapabilityResult(ok=False, error=f"不支持的文件格式或加载失败: {path}")
        splitter = RecursiveSplitter()
        chunks = splitter.split(docs, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        for c in chunks:
            c.metadata = dict(c.metadata or {})
            c.metadata.setdefault("source", path)
        try:
            res = self._store.upsert(kb_dir.name, chunks)
        except Exception as e:
            return CapabilityResult(ok=False, data={}, error=f"入库失败：{e}")
        return CapabilityResult(ok=True, data={
            "ingested": True, "chunk_count": len(chunks), "count": res.get("count", 0),
        })

    # ---- stats / clear ----

    def stats(self, name: str) -> CapabilityResult:
        kb_dir = self._resolve(name)
        if kb_dir is None:
            return CapabilityResult(ok=False, error=f"知识库不存在：{name!r}")
        return CapabilityResult(ok=True, data=self._store.stats(kb_dir.name))

    def clear(self, name: str) -> CapabilityResult:
        kb_dir = self._resolve(name)
        if kb_dir is None:
            return CapabilityResult(ok=False, error=f"知识库不存在：{name!r}")
        return CapabilityResult(ok=True, data={"cleared": self._store.clear(kb_dir.name)})

    # ---- 内部 ----

    def _resolve(self, name: str) -> Optional[Path]:
        """把知识库名解析到目录内，防越权（.. / 绝对路径）。"""
        try:
            p = (self._root / name).resolve()
        except OSError:
            return None
        return p if p.is_dir() and p.is_relative_to(self._root.resolve()) else None

    @staticmethod
    def _read_meta(kb_dir: Path) -> tuple[dict, str]:
        """读取知识库元信息：可选 ``kb.yaml``（name/description 等）。"""
        kb_yaml = kb_dir / "kb.yaml"
        if kb_yaml.is_file():
            try:
                meta = yaml.safe_load(kb_yaml.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), kb_yaml.name
        return {}, ""


# ---- 总线装配 ----

def register(bus, store: Optional[KnowledgeStore] = None) -> None:
    """注册 knowledge 门面。

    Args:
        store: 知识库存储后端（默认 ``InMemoryKnowledgeStore``）。自定义后端须实现
            upsert/search/stats/clear。
    """
    engine = KnowledgeEngine(store=store)

    def handle(d: Dispatch) -> CapabilityResult:
        op = d.op or (d.payload.get("op") or "")
        name = str(d.payload.get("name") or "")
        if op == "find":
            return engine.find(str(d.payload.get("keyword") or ""))
        if op == "read":
            return engine.read(name)
        if op == "search":
            return engine.search(
                name,
                str(d.payload.get("query") or ""),
                top_k=int(d.payload.get("top_k", 5)),
                score_threshold=float(d.payload.get("score_threshold", 0.0)),
            )
        if op == "ingest":
            return engine.ingest(
                name,
                str(d.payload.get("path") or ""),
                chunk_size=int(d.payload.get("chunk_size", 1000)),
                chunk_overlap=int(d.payload.get("chunk_overlap", 200)),
            )
        if op == "stats":
            return engine.stats(name)
        if op == "clear":
            return engine.clear(name)
        return CapabilityResult(
            ok=False, error=f"未知操作 {op}（期望 find/read/search/ingest/stats/clear）",
        )

    # 单一契约点 knowledge（manifest 只暴露这一个知识库入口），op 分发（对齐 skill/tools 范式）
    bus.on("knowledge", handle, meta={
        "description": DESCRIPTION,
        "ops": ["find", "read", "search", "ingest", "stats", "clear"],
    })
