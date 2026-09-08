"""内置能力插件：context —— 决策输入节点（本次会话上下文，默认采集 payload 素材）。

语义领域：**本次会话**要喂给决策者的上下文素材（会话历史消息 / 上传文本 / 用户指令）。
素材**不是一个一个独立节点**，而是本节点内部的采集来源（collectors）——旧架构 Source/Filter
的碎片化在这里收敛为 context 一个节点。

产出约定：``data["messages"]`` 为注入决策者的消息序列（历史 + 素材）；无任何素材 → 降级为仅携带任务目标
（单核最小可运行，不报错、不阻塞）。

装配（零耦合，可拆卸）：
- 默认从 dispatch payload 采集：``payload["session_messages"]``（会话历史消息，
  shape: list[{role, content}]）、``payload["uploaded"]``（list[str] 上传文本）、
  ``payload["rag"]``（list[str] RAG 引用）；
- ``build_context(history=, uploaded=, rag=)`` 可注入自定义采集器替换各来源
  （如接旧 DataService 历史 / 旧 knowledge loader / vector_store 召回）；
- 本节点不 dispatch 任何其他节点（memory 是平级 peer，互不认识）。
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

from libcore.kernel.bus import Dispatch, CapabilityResult, Notice
from libcore.llm.spi import LLMMsg

_logger = logging.getLogger("libcore.capability.context")

DESCRIPTION = (
    "决策输入节点：内核每轮决策前点名我，产出本次会话上下文消息序列（data['messages']）注入决策者。"
    "默认采集 payload 中的会话历史/上传/RAG；可注入自定义采集器；无素材降级为最简默认。"
)

# 采集器类型：接收 Dispatch（payload 含 goal/会话素材），返回要并入 context 的文本（可为空）
Collector = Callable[[Dispatch], Optional[str]]


# ── 裁剪窗口（trimmer）：context 统一后处理，不拆子节点 ────────────────

def trim_history(
    messages: List[dict],
    *,
    last_n: int = 10,
    exclude_last_user_if_present: bool = True,
) -> List[dict]:
    """裁剪会话历史：保留最近 N 条；若最后一条是 user 则排除（避免与当前指令重复）。

    对齐旧 ``MessageTrimmerLastNFilter`` 语义（message_builder L101-L118）。
    """
    if not messages:
        return []
    if exclude_last_user_if_present and messages[-1].get("role") == "user":
        if len(messages) > 1:
            return messages[-(last_n + 1):-1] if len(messages) > last_n else messages[:-1]
        return []
    return messages[-last_n:] if len(messages) > last_n else messages


# ── 内置采集器（从 dispatch payload 读取，零外部依赖）──────────────────

def _history_from_payload(d: Dispatch, *, last_n: int = 10) -> Optional[str]:
    """历史采集：从 payload["session_messages"] 拼出 role:content 行（对齐旧 HistoryLoaderSource）。

    先经 ``trim_history`` 裁剪到最近 N 条（对齐旧 MessageTrimmerLastNFilter），
    避免超长会话撑爆上下文。
    """
    msgs = d.payload.get("session_messages") or []
    msgs = trim_history(msgs, last_n=last_n)
    lines: List[str] = []
    for m in msgs:
        role = m.get("role") if isinstance(m, dict) else "user"
        content = str(m.get("content", "")).strip() if isinstance(m, dict) else str(m).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else None


def _uploaded_from_payload(d: Dispatch) -> Optional[str]:
    """上传采集：从 payload["uploaded"]（list[str] 文本）并出。"""
    items = d.payload.get("uploaded") or []
    texts = [str(t).strip() for t in items if isinstance(t, str) and t.strip()]
    return "\n\n".join(texts) if texts else None


def _rag_from_payload(d: Dispatch) -> Optional[str]:
    """RAG 采集：从 payload["rag"]（list[str] 引用）并出。"""
    refs = d.payload.get("rag") or []
    texts = [str(r).strip() for r in refs if isinstance(r, str) and r.strip()]
    return "\n".join(texts) if texts else None


# ── RAG 召回采集器（可注入检索后端，不拆子节点）────────────────────────

def build_rag_retriever(
    *,
    search: Optional[Callable[[str, dict], List[str]]] = None,
    top_k: int = 3,
    score_threshold: float = 0.0,
    label: str = "知识库引用（供参考）",
) -> Collector:
    """装配 RAG 召回采集器：把"查询→命中文档文本"的检索函数并入 context。

    Args:
        search: 向量检索函数，签名 ``search(query, options) -> list[str]``（命中文本）。
            缺省用从 payload["rag"] 读取的内置引用；传自定义函数可替换
            （如包装旧 KnowledgeFacade.search 或 vector_store 召回）。
        top_k/score_threshold: 检索参数，透传给 search 的 options（对齐旧 RagRetrieverBasicSource）。
        label: 注入文本段首标签。

    Returns:
        一个 RAG 采集器（可作为 context 节点 handler 的数据源）。
    """
    def collect(d: Dispatch) -> Optional[str]:
        query = str(d.payload.get("goal") or "")
        options = {"top_k": top_k, "score_threshold": score_threshold}
        if search is not None:
            try:
                hits = search(query, options) or []
            except Exception:
                hits = []  # 检索异常不致命：降级为无 RAG 引用（无感原则）
        else:
            hits = [str(r).strip() for r in (d.payload.get("rag") or [])
                    if isinstance(r, str) and r.strip()]
        texts = [h for h in hits if isinstance(h, str) and h.strip()]
        if not texts:
            return None
        return f"{label}：\n" + "\n".join(f"- {t}" for t in texts[:top_k])

    return collect


# ── 装配：组合采集器（不拆子节点）──────────────────────────────────────

def build_context(
    *,
    history: Optional[Collector] = None,
    uploaded: Optional[Collector] = None,
    rag: Optional[Collector] = None,
    rag_search: Optional[Callable[[str, dict], List[str]]] = None,
    rag_top_k: int = 3,
    last_n: int = 10,
) -> Collector:
    """装配 context 节点：把若干采集器合并成单节点产出（缺省来源自动跳过）。

    Args:
        history/uploaded/rag: 各有采集函数，接收 Dispatch，返回一段文本或 None/""（自动跳过）。
            缺省用从 payload 读取的内置采集器；传自定义函数可替换（如接真实数据源）。
        rag_search: RAG 向量检索函数 ``search(query, options) -> list[str]``；缺省用
            payload["rag"] 引用，传自定义函数可接真实向量库召回（对齐旧 RagRetrieverBasicSource）。
        rag_top_k: RAG 召回条数。
        last_n: 历史裁剪窗口（对齐旧 MessageTrimmerLastNFilter），仅对默认历史采集器生效。

    Returns:
        一个组合采集器（可作为 context 节点 handler 的数据源）。
    """
    hist = history or (lambda d: _history_from_payload(d, last_n=last_n))
    up = uploaded or _uploaded_from_payload
    rg = rag or build_rag_retriever(search=rag_search, top_k=rag_top_k)

    def gather(d: Dispatch) -> str:
        parts: List[str] = []
        for c in (hist, up, rg):
            text = c(d)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)

    return gather


def _normalize_goal(goal) -> str:
    """规范化任务目标为干净文本（符合行业命名：goal 是纯文本指令）。

    - dict 包裹（如 ``{"goal": "..."}``）→ 提取 ``goal["goal"]``；
    - 纯字符串 → 原样返回；
    - 其他 → str() 兜底。
    避免把 ``{'goal': ...}`` 嵌套冗余拼进注入文本。
    """
    if isinstance(goal, dict):
        inner = goal.get("goal")
        if isinstance(inner, str) and inner.strip():
            return inner.strip()
        return str(goal).strip()
    return str(goal).strip()


def _default_collector(d: Dispatch) -> str:
    """默认采集器：上传/RAG 素材 + 任务目标（不含历史，历史单独转消息序列）。"""
    parts: List[str] = []
    for c in (_uploaded_from_payload, _rag_from_payload):
        text = c(d)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    goal = _normalize_goal(d.payload.get("goal"))
    if goal:
        parts.append(f"当前任务目标：{goal}")
    return "\n\n".join(parts)


# ── register：挂 context 输入节点（默认从 payload 采集）───────────────

def register(bus, collector: Optional[Collector] = None, *, backend=None):
    """注册 context 输入节点。

    Args:
        collector: 可传装配好的采集器；缺省从 payload 采集。
        backend: 可注入会话存储（实现 ``SessionStore`` 契约，含
            ``get_history(session_id)`` 返回 ``list[{role, content}]``）。
            缺省用 context 内置的临时历史（一个纯数组，不区分会话）；
            注入后**替换**内置历史——内部数组清空、append 禁用，历史完全交给
            backend（按会话隔离 + 可持久化）。会话数据本体由 session 插件收敛，
            context 不再直接碰 store（解耦）。

    Returns:
        ``append(role, content)`` 写入口：向 context 内置临时历史追加一条消息。
        仅当未注入 backend 时有效；注入后 append 为 no-op（历史由
        backend 管理，避免内部数组泄漏/混淆）。
    """
    # 素材采集器（上传/RAG/任务目标），不含历史——历史单独转成消息序列，避免重复注入
    gather = collector or _default_collector
    # context 内置临时历史：纯数组（不区分会话，多个任务混在一起）
    _history: list = []
    # 注入 backend 时替换内置历史：清空数组 + 禁用 append
    _has_session = backend is not None
    if _has_session:
        _history = []

    def append(role: str, content: str) -> None:
        if _has_session:
            return  # 有 session：历史由 backend 管理，内部数组禁用
        _history.append({"role": role, "content": content})

    async def handle(d: Dispatch) -> CapabilityResult:
        # 历史消息原样转成消息序列（保留 user/assistant 交替时序）
        msgs: List[LLMMsg] = []
        history = await _load_history(d, backend, _history, bus)
        # 裁剪策略由配置驱动（payload["trim"]），决定"模型看会话的哪些内容"；
        # 无配置时用默认（保留最近 10 条，含最后一条 user）
        trim = d.payload.get("trim") or {}
        history = trim_history(
            history,
            last_n=int(trim.get("last_n", 10)),
            exclude_last_user_if_present=bool(trim.get("exclude_last_user_if_present", False)),
        )
        for m in history:
            role = m.get("role") if isinstance(m, dict) else "user"
            content = str(m.get("content", "")).strip() if isinstance(m, dict) else str(m).strip()
            if content:
                msgs.append(LLMMsg(role, content))
        # 上传 / RAG / 任务目标素材作为 user 消息追加
        extra = gather(d)
        if extra:
            msgs.append(LLMMsg("user", extra))
        return CapabilityResult(ok=True, data={"messages": msgs})

    bus.on("context", handle, meta={
        "description": DESCRIPTION,
        "ops": ["run"],
    })
    return append


async def _load_history(d: Dispatch, backend, default_history, bus) -> List[dict]:
    """从历史来源或 payload 读取历史消息（统一为 list[{role, content}]）。

    优先：注入 backend 且带 session_id → 从后端按会话读历史。
    其次：context 内置临时历史（纯数组，不区分会话）。
    回退：payload["session_messages"]（调用方喂）。
    存储异常不致命：降级为内置历史（无感原则），但广播 ``context.store_error``
    让故障可观测（不静默吞错，符合"系统运行不漏"）。
    """
    session_id = d.payload.get("session_id")
    if backend is not None and session_id:
        try:
            history = backend.get_history(session_id) or []
            if history:
                return [
                    {"role": m.get("role", "user"), "content": m.get("content", "")}
                    for m in history
                    if isinstance(m, dict) and m.get("content")
                ]
        except Exception as e:  # noqa: BLE001 存储异常不致命，但需可观测
            _logger.warning("context session read failed: %s", e)
            try:
                await bus.publish(Notice(
                    topic="context.store_error",
                    payload={"session_id": session_id, "error": str(e)},
                ))
            except Exception:  # noqa: BLE001 广播失败不致命
                pass
    if default_history:
        return list(default_history)
    return d.payload.get("session_messages") or []
