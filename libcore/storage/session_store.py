"""会话存储契约（抽象接口，不绑定任何存储实现）。

对齐"组件只依赖契约、不依赖实现"原则：context 等插件依赖本契约，
不关心底层是 SQLite / 文件 / 内存 / 远程。开发者想换存储，就自己实现
本接口 + 转换逻辑（把 dataclass 转成自己的存储格式）。

契约只定义"存什么、取什么"（数据形状），不定义"怎么存"。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from libcore.storage.contracts import AgentSessionRecord


class SessionStore(ABC):
    """会话存储契约：完整会话（AgentSessionRecord）的读写。

    会话是完整实体（内嵌消息列表）。实现方负责把 dataclass 转成自己的
    存储格式（SQL 行 / JSON 文件 / 远程 API），并转回 dataclass。
    """

    @abstractmethod
    def get_agent_session(self, session_id: str) -> Optional[AgentSessionRecord]:
        """按 id 读回完整会话（含内嵌消息列表）；不存在返回 None。"""

    @abstractmethod
    def save_agent_session(self, session: AgentSessionRecord) -> None:
        """保存完整会话（含内嵌消息列表）。"""

    @abstractmethod
    def list_agent_sessions(self, chat_id: str) -> List[AgentSessionRecord]:
        """按 chat_id 列出会话（不含消息列表，仅会话元数据）。"""

    @abstractmethod
    def delete_agent_session(self, session_id: str) -> None:
        """删除会话及其消息。"""
