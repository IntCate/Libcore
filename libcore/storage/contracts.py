"""存储实体契约（原生重写，不 import 旧 app.runtime）。

对齐旧 ``storage_components/contracts.py``，但按 libcore 收敛为精简实体：
只保留 libcore 真正需要的 chat / message / agent_session / checkpoint。
纯 dataclass，不依赖 SQLAlchemy、不绑定任何数据库。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MessageRecord:
    """单条对话消息。"""
    id: str
    chat_id: str
    role: str
    message_type: str = "normal"
    content: str = ""
    reasoning_content: Optional[str] = None
    created_at: str = ""
    model: Optional[str] = None
    files: List[Any] = field(default_factory=list)
    agent_session_id: Optional[str] = None
    agent_node: Optional[str] = None
    agent_step: Optional[int] = None
    agent_metadata: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentSessionRecord:
    """智能体会话（完整实体，内嵌消息列表）。

    会话是完整实体：对话消息、状态、元数据都收敛于此。
    消息列表元素用 MessageRecord（内嵌元素类型，非独立实体）。
    """
    id: str
    chat_id: str
    created_at: str = ""
    updated_at: str = ""
    graph_state: Optional[Dict[str, Any]] = None
    current_node: str = ""
    step_count: int = 0
    messages: List[MessageRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckpointRecord:
    """内核状态检查点（checkpoint / 长任务断点）。"""
    id: str
    scope: str
    state: Dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionRecord:
    """会话数据本体（轻量，供插件内部使用）。"""
    id: str
    messages: List[dict] = field(default_factory=list)   # [{role, content}, ...]
    graph_state: Optional[dict] = None
    step_count: int = 0
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class MemoryRecord:
    """单条记忆记录。``tags``/``importance`` 预留排序与关联。"""
    id: str = field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:16]}")
    session_id: str = ""       # 归属会话；空 = 全局记忆
    content: str = ""          # 记忆正文（偏好/事实/摘要）
    kind: str = "fact"         # "summary" | "fact" | "skill_ref"
    importance: float = 0.5
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
