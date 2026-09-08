"""JSON 文件存储接入插件集成测试：验证文件存储真正跑通完整链路。

验证点：
1. JsonFileSessionStore 作为 session_backend 注入 context：多轮对话历史回灌；
2. JsonFileMemoryBackend 作为 backend 注入 memory：跨会话记忆检索注入决策；
3. 跨实例持久化：文件存储真正落盘，新实例能读到旧数据。
"""
from __future__ import annotations

import asyncio

from libcore.kernel.bus import EventBus
from libcore.kernel.agent import AgentLoop, ReasonProvider, Action
from libcore.plugins.capabilities import context as context_cap
from libcore.plugins.capabilities import memory as memory_cap
from libcore.storage.json_file_store import (
    JsonFileMemoryBackend,
    JsonFileSessionStore,
)


class CaptureReason(ReasonProvider):
    """决策者：捕获注入的 input_fragments，然后 finish。"""

    def __init__(self):
        self.seen: list = []
        self.seen_goal: str = ""

    async def decide(self, ctx):
        for target in ("context", "memory"):
            for frag in ctx.input_fragments.get(target, []):
                self.seen.append((frag.role, frag.content))
        return Action(finish=True)


def _run_chat(bus, reason, goal, session_id=None, input_nodes=None):
    loop = AgentLoop(bus, reason, input_nodes=input_nodes)
    return asyncio.run(loop.run({"goal": goal}, session_id=session_id))


class TestJsonSessionStoreInContext:
    """JsonFileSessionStore 作为 backend 注入 context：多轮历史回灌。"""

    def test_multi_turn_history_reinjected(self, tmp_path):
        backend = JsonFileSessionStore(str(tmp_path / "sessions"))
        bus = EventBus()
        context_cap.register(bus, backend=backend)
        session_id = "sess-json-1"

        # 第 1 轮：写入历史
        backend.append_message(session_id, "user", "我叫小明")
        backend.append_message(session_id, "assistant", "你好，小明")

        # 第 2 轮：决策者应看到历史（user + assistant）+ 当前目标
        reason = CaptureReason()
        _run_chat(bus, reason, "我叫什么？", session_id=session_id,
                  input_nodes=[{"target": "context", "slot": "user"}])
        roles = [r for r, _ in reason.seen]
        texts = [c for _, c in reason.seen]
        assert "user" in roles and "assistant" in roles, f"应回灌 user+assistant：{reason.seen}"
        assert any("我叫小明" in t for t in texts), f"应回灌前文：{texts}"
        assert any("你好，小明" in t for t in texts), f"应回灌 assistant 回复：{texts}"

    def test_session_isolation_across_sessions(self, tmp_path):
        backend = JsonFileSessionStore(str(tmp_path / "sessions"))
        bus = EventBus()
        context_cap.register(bus, backend=backend)
        backend.append_message("sess-a", "user", "A的历史")
        reason_b = CaptureReason()
        _run_chat(bus, reason_b, "继续", session_id="sess-b",
                  input_nodes=[{"target": "context", "slot": "user"}])
        texts_b = [c for _, c in reason_b.seen]
        assert not any("A的历史" in t for t in texts_b), f"会话 B 不应看到 A 的历史：{texts_b}"


class TestJsonMemoryBackendInMemory:
    """JsonFileMemoryBackend 作为 backend 注入 memory：跨会话记忆检索注入决策。"""

    def test_memory_retrieval_injected(self, tmp_path):
        backend = JsonFileMemoryBackend(str(tmp_path / "memories"))
        backend.remember("用户喜欢喝美式咖啡", session_id="s1", kind="fact", importance=0.9)
        bus = EventBus()
        memory_cap.register(bus, backend=backend)
        reason = CaptureReason()
        _run_chat(bus, reason, "美式咖啡", session_id="s1",
                  input_nodes=[{"target": "memory", "slot": "user"}])
        texts = [c for _, c in reason.seen]
        assert any("美式咖啡" in t for t in texts), f"记忆应注入决策：{texts}"

    def test_memory_persists_across_instances(self, tmp_path):
        """跨实例持久化：新 backend 实例能读到旧数据。"""
        dir_path = str(tmp_path / "memories")
        backend1 = JsonFileMemoryBackend(dir_path)
        backend1.remember("跨实例记忆", session_id="s1", importance=0.5)
        backend2 = JsonFileMemoryBackend(dir_path)
        bus = EventBus()
        memory_cap.register(bus, backend=backend2)
        reason = CaptureReason()
        _run_chat(bus, reason, "跨实例", session_id="s1",
                  input_nodes=[{"target": "memory", "slot": "user"}])
        texts = [c for _, c in reason.seen]
        assert any("跨实例" in t for t in texts), f"新实例应读到旧记忆：{texts}"
