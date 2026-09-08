"""内置能力插件：session —— 会话数据本体（历史 + 状态），通过 SessionStore 解耦存储。

语义领域：**一次连续对话的完整状态**（历史消息 / 图状态 / 步数 / 元数据）。
与 context/prompt/memory 平级 peer，互不认识。产出约定：``data["messages"]``（历史消息序列）。

装配（零耦合，可拆卸）：
- ``register(bus)`` 默认挂 ``DefaultSessionStore``（包装进程内内存存储）；
- ``register(bus, backend=...)`` 可注入自定义后端（实现 ``SessionStore`` 契约，
  如 SqliteDataStore / JsonFileSessionStore / 远程），签名见 ``SessionStore``；
- 存储异常不致命：降级为空历史（对齐无感原则），但广播 ``session.store_error`` 可观测。

**解耦目标**：会话数据本体收敛到本插件，context 不再直接碰 store，
存储通过 ``SessionStore`` 契约任意组装（SQLite / 文件 / 内存 / 远程）。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from libcore.kernel.bus import Dispatch, CapabilityResult, Notice
from libcore.llm.spi import LLMMsg
from libcore.storage.contracts import AgentSessionRecord, MessageRecord, SessionRecord
from libcore.storage.session_store import SessionStore

_logger = logging.getLogger("libcore.capability.session")

DESCRIPTION = (
    "决策输入节点：内核每轮决策前点名我，产出本次会话历史消息序列（data['messages']）注入决策者。"
    "会话数据本体（历史+状态）收敛于此，通过 SessionStore 契约解耦存储。"
    "无历史时降级为空序列，不报错。"
)


class InMemorySessionStore:
    """进程内会话存储：dict 持有 SessionRecord。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, SessionRecord] = {}

    def get(self, session_id: str) -> Optional[SessionRecord]:
        return self._sessions.get(session_id)

    def save(self, session: SessionRecord) -> None:
        self._sessions[session.id] = session


class DefaultSessionStore(SessionStore):
    """默认会话存储：包装进程内 ``InMemorySessionStore``（零依赖可裸跑）。

    实现 ``SessionStore`` 契约（含 get_history / append_message / save_session
    便捷方法），供 session / context 插件注入。
    """

    def __init__(self, store: Optional[InMemorySessionStore] = None) -> None:
        self._store = store or InMemorySessionStore()

    def get_agent_session(self, session_id: str):
        rec = self._store.get(session_id)
        if rec is None:
            return None
        return AgentSessionRecord(
            id=rec.id, chat_id=rec.id, graph_state=rec.graph_state,
            step_count=rec.step_count, metadata=rec.metadata,
            messages=[MessageRecord(
                id=f"{rec.id}-{i}", chat_id=rec.id,
                role=m.get("role", "user"), content=m.get("content", ""),
            ) for i, m in enumerate(rec.messages)],
        )

    def save_agent_session(self, session) -> None:
        self._store.save(SessionRecord(
            id=session.id, messages=[
                {"role": m.role, "content": m.content} for m in session.messages
            ], graph_state=session.graph_state, step_count=session.step_count,
            metadata=session.metadata,
        ))

    def list_agent_sessions(self, chat_id: str):
        return [self.get_agent_session(s.id) for s in self._store._sessions.values()
                if s.id == chat_id]

    def delete_agent_session(self, session_id: str) -> None:
        self._store._sessions.pop(session_id, None)

    def get_history(self, session_id: str) -> List[dict]:
        rec = self._store.get(session_id)
        return list(rec.messages) if rec else []

    def append_message(self, session_id: str, role: str, content: str) -> None:
        rec = self._store.get(session_id) or SessionRecord(id=session_id)
        rec.messages.append({"role": role, "content": content})
        self._store.save(rec)

    def save_session(self, session: SessionRecord) -> None:
        self._store.save(session)


_SESSION_DEFAULT = DefaultSessionStore()


# ── register：挂 session 输入节点（默认内存后端）────────────────────────

def register(bus, backend: Optional[SessionStore] = None) -> None:
    """注册 session 输入节点。

    Args:
        backend: 会话存储（默认 ``DefaultSessionStore``）。自定义后端须实现
            ``SessionStore`` 契约（含 ``get_history``）。
    """
    b = backend or _SESSION_DEFAULT

    async def handle(d: Dispatch) -> CapabilityResult:
        session_id = str(d.payload.get("session_id") or "")
        msgs: List[LLMMsg] = []
        if session_id:
            try:
                history = b.get_history(session_id) or []
            except Exception as e:  # noqa: BLE001 存储异常不致命，但需可观测
                _logger.warning("session get_history failed: %s", e)
                try:
                    await bus.publish(Notice(
                        topic="session.store_error",
                        payload={"session_id": session_id, "error": str(e)},
                    ))
                except Exception:  # noqa: BLE001 广播失败不致命
                    pass
                history = []
            for m in history:
                role = m.get("role") if isinstance(m, dict) else "user"
                content = str(m.get("content", "")).strip() if isinstance(m, dict) else str(m).strip()
                if content:
                    msgs.append(LLMMsg(role, content))
        return CapabilityResult(ok=True, data={"messages": msgs})

    bus.on("session", handle, meta={
        "description": DESCRIPTION,
        "label": "session",
        "ops": ["run"],
    })
