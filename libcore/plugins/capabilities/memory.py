"""内置能力插件：memory —— 决策输入节点（跨会话记忆，默认内存实现）。

语义领域：**跨会话**的持久记忆（用户偏好 / 历史事实 / 会话摘要）。与 context/prompt 平级、
互不认识，独立 enabled:false 可拆卸。产出约定：``data["messages"]``。

默认实现提供一个**零依赖的内存情景记忆存储**（对齐旧 MemoryEpisodicFTSStore 的
中文二元切分关键词检索 + 会话作用域过滤），保证"零配置可裸跑、有记忆可用"（单核最小可运行 + 有是强化）。

装配（零耦合，可拆卸）：
- ``register(bus)`` 默认挂内存存储；``register(bus, backend=...)`` 可注入自定义后端
  （如包装旧 MemoryRetrieverHybrid 或 SQLite/向量存储），签名见 ``MemoryBackend``；
- 检索/存储异常不致命：降级为空记忆（对齐无感原则）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from libcore.kernel.bus import Dispatch, CapabilityResult
from libcore.llm.spi import LLMMsg
from libcore.storage.contracts import MemoryRecord
from libcore.storage.memory_store import MemoryBackend, _keywords

DESCRIPTION = (
    "决策输入节点：内核每轮决策前点名我，产出跨会话记忆消息序列（data['messages']）注入决策者。"
    "默认内存情景存储；可注入外部记忆后端。检索异常/无命中时降级为空文本。"
)


# ── 记忆契约（零依赖，轻量）─────────────────────────────────────────────

@dataclass
class InMemoryMemoryStore:
    """进程内情景记忆存储：关键词检索 + 会话过滤 + importance 排序。"""

    records: Dict[str, MemoryRecord] = field(default_factory=dict)

    def save(self, record: MemoryRecord) -> None:
        self.records[record.id] = record

    def retrieve(self, query: str, *, session_id: str, limit: int = 5) -> List[MemoryRecord]:
        kws = _keywords(query)
        scored: List[tuple] = []
        for rec in self.records.values():
            if rec.session_id and rec.session_id != session_id:
                continue  # 只取本会话（空=全局）记忆
            hay = f"{rec.content} {' '.join(rec.tags)}".lower()
            hits = sum(1 for kw in kws if kw and kw in hay)
            if hits:
                scored.append((hits, rec.importance, rec))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [r for _h, _i, r in scored[:limit]]


# ── 对外记忆后端（可注入）───────────────────────────────────────────────

class DefaultMemoryBackend(MemoryBackend):
    """默认记忆后端：包装进程内 ``InMemoryMemoryStore``。

    除检索外还公开 ``remember(record)``，供会话运行结束后把摘要/偏好写进记忆
    （对齐旧 MemoryFacade.record_run 的意图；无外部持久化 = 进程内有效）。
    """
    def __init__(self, store: Optional[InMemoryMemoryStore] = None) -> None:
        self.store = store or InMemoryMemoryStore()

    def remember(self, content: str, *, session_id: str = "", kind: str = "fact",
                 importance: float = 0.5, tags: Optional[List[str]] = None) -> MemoryRecord:
        rec = MemoryRecord(session_id=session_id, content=content, kind=kind,
                           importance=importance, tags=list(tags or []))
        self.store.save(rec)
        return rec

    def retrieve(self, query: str, *, session_id: str, limit: int = 5) -> List[str]:
        recs = self.store.retrieve(query, session_id=session_id, limit=limit)
        return [r.content for r in recs]

    def consolidate(self, messages: List[dict], *, session_id: str,
                    consolidator: Optional[SessionSummaryConsolidator] = None) -> MemoryRecord:
        """把一次运行的对话巩固为摘要并写入记忆（B.1 层 1 写记忆闭环）。

        Args:
            messages: 该次运行的完整对话消息 [{role, content}, ...]
            session_id: 记忆归属作用域
            consolidator: 摘要巩固器（缺省用零依赖统计摘要，LLM 可注入）。

        Returns:
            写入的摘要记忆记录。
        """
        c = consolidator or SessionSummaryConsolidator()
        summary = c.summarize(messages)
        return self.remember(summary, session_id=session_id, kind="summary",
                             importance=0.8, tags=["session_summary"])


# ── 混合记忆检索（对齐旧 MemoryRetrieverHybrid 融合逻辑）────────────────

class HybridMemoryBackend(MemoryBackend):
    """混合记忆后端：向量召回 ∪ 全文召回 → 按 id 去重 → importance 排序 → top_k。

    复刻旧 ``MemoryRetrieverHybrid`` 的融合语义（B.2 层 2），但存储可注入、
    不依赖旧 memory_registry（零耦合）。任一存储不可用 → 跳过该路（降级可用）。

    Args:
        vector_store: 向量召回存储（须实现 ``search_keyword(query, limit)``），可为 None。
        fts_store:    全文召回存储（须实现 ``search_keyword(query, limit)``），可为 None。
        skill_store:  技能引用召回存储（可选），可为 None。
        weights:      各路召回权重（向量 1.2 / 全文 1.0 / 技能 0.8，对齐旧实现）。
    """
    def __init__(self, *, vector_store=None, fts_store=None, skill_store=None,
                 weights: Optional[Dict[str, float]] = None) -> None:
        self._vector_store = vector_store
        self._fts_store = fts_store
        self._skill_store = skill_store
        self._weights = weights or {"vector": 1.2, "fts": 1.0, "skill": 0.8}

    def _recall(self, store, query: str, limit: int) -> List[MemoryRecord]:
        if store is None:
            return []
        try:
            return list(store.search_keyword(query, limit=limit))
        except Exception:
            return []  # 该路不可用 → 跳过（降级可用，对齐 IM-2 精神）

    def retrieve(self, query: str, *, session_id: str, limit: int = 5) -> List[str]:
        merged: Dict[str, MemoryRecord] = {}
        for store, weight in (
            (self._vector_store, self._weights.get("vector", 1.2)),
            (self._fts_store, self._weights.get("fts", 1.0)),
            (self._skill_store, self._weights.get("skill", 0.8)),
        ):
            for rec in self._recall(store, query, limit):
                self._merge(merged, rec, weight)
        # 会话过滤（skill 记录 session_id 为空，不过滤）
        result = [
            rec for rec in merged.values()
            if not rec.session_id or rec.session_id == session_id
        ]
        result.sort(
            key=lambda r: (r.importance * (r.importance or 0.5), r.created_at),
            reverse=True,
        )
        return [r.content for r in result[:limit]]

    @staticmethod
    def _merge(merged: Dict[str, MemoryRecord], rec: MemoryRecord, weight: float) -> None:
        existing = merged.get(rec.id)
        if existing is None:
            rec.importance = max(rec.importance, weight * 0.5)
            merged[rec.id] = rec
        else:
            existing.importance = max(existing.importance, weight * 0.5)


# ── 会话摘要巩固（对齐旧 MemorySessionSummaryConsolidator）──────────────

_MAX_INPUT_CHARS = 12000


class SessionSummaryConsolidator:
    """会话摘要巩固器：对话消息 → 摘要文本（B.1 层 1）。

    复刻旧 ``MemorySessionSummaryConsolidator`` 语义，但 LLM 可注入、不依赖旧
    auxiliary_fallback_router（零耦合）。LLM 未注入/调用异常 → 降级统计摘要
    （辅助任务可降级，不阻塞主流程，对齐 IM-2 精神）。

    Args:
        llm: 摘要生成函数 ``llm(prompt) -> str``（可注入真实辅助 LLM 调用）。
            缺省 None → 直接降级统计摘要（零配置可裸跑）。
        max_input_chars: 摘要输入截断（防止超长会话撑爆辅助 LLM 上下文）。
    """
    def __init__(self, *, llm: Optional[Callable[[str], str]] = None,
                 max_input_chars: int = _MAX_INPUT_CHARS) -> None:
        self._llm = llm
        self._max_input_chars = max_input_chars

    def _truncate_input(self, messages: List[dict]) -> str:
        parts: List[str] = []
        total = 0
        for msg in messages:
            role = msg.get("role", "user")
            content = str(msg.get("content", "")).strip()
            if not content:
                continue
            line = f"{role}: {content}"
            if total + len(line) > self._max_input_chars:
                break
            parts.append(line)
            total += len(line)
        return "\n".join(parts)

    def _fallback_summary(self, messages: List[dict]) -> str:
        user_count = sum(1 for m in messages if m.get("role") == "user")
        first_user = ""
        for m in messages:
            if m.get("role") == "user" and m.get("content"):
                first_user = str(m["content"])[:200]
                break
        tail = f" 首条用户消息：{first_user}" if first_user else ""
        return f"本会话共 {len(messages)} 条消息（其中用户 {user_count} 条）。{tail}"

    def summarize(self, messages: List[dict]) -> str:
        """把对话巩固为摘要文本（LLM 不可用/异常 → 降级统计摘要）。"""
        text = self._truncate_input(messages)
        if not text:
            return "（空会话，无摘要）"
        if self._llm is None:
            return self._fallback_summary(messages)
        prompt = (
            "请把以下对话巩固为简洁的会话摘要（中文，200 字以内），"
            "保留用户偏好、关键结论、未完成任务：\n\n" + text
        )
        try:
            result = self._llm(prompt)
        except Exception:
            return self._fallback_summary(messages)
        if isinstance(result, str) and result.strip():
            return result.strip()
        return self._fallback_summary(messages)


_MEMORY_DEFAULT = DefaultMemoryBackend()


# ── register：挂 memory 输入节点（默认内存后端）────────────────────────

def register(bus, backend: Optional[MemoryBackend] = None) -> None:
    """注册 memory 输入节点。

    Args:
        backend: 记忆后端（默认 ``DefaultMemoryBackend``）。自定义后端须实现 ``retrieve``。
    """
    memory = backend or _MEMORY_DEFAULT

    def handle(d: Dispatch) -> CapabilityResult:
        query = str(d.payload.get("goal") or "")
        session_id = str(d.payload.get("session_id") or "")
        try:
            hits = memory.retrieve(query, session_id=session_id, limit=5) or []
        except Exception:
            hits = []  # 检索异常不致命：降级为空记忆（无感原则）
        text = ""
        if hits:
            text = "历史记忆（供参考）：\n" + "\n".join(f"- {h}" for h in hits)
        msgs = [LLMMsg("user", text)] if text else []
        return CapabilityResult(ok=True, data={"messages": msgs})

    bus.on("memory", handle, meta={
        "description": DESCRIPTION,
        "label": "memory",
        "ops": ["run"],
    })
