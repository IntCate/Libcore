"""session 插件验收测试：会话数据本体（历史+状态）收敛于插件，通过 SessionStore 解耦存储。

验证点（对应验收目标）：
1. session 插件作为输入节点：注入会话历史消息序列（data['messages']）；
2. SessionStore 契约解耦：context 不再直接碰 store，从 session_store 取历史；
3. 默认内存存储可裸跑（零依赖）；自定义存储可注入（任意组装）；
4. 存储异常不致命：降级为空历史，但广播 session.store_error 可观测。

不依赖 pytest-asyncio：用 asyncio.run() 在同步测试函数内跑异步闭环。
"""
from __future__ import annotations

import asyncio

from libcore.kernel.bus import EventBus, Dispatch, CapabilityResult, Notice
from libcore.kernel.agent import AgentLoop, ReasonProvider, Action
from libcore.plugins.capabilities import session as session_cap
from libcore.plugins.capabilities import context as context_cap
from libcore.storage.session_store import SessionStore


class CaptureReason(ReasonProvider):
    """决策者：捕获注入的 input_fragments，然后 finish。"""

    def __init__(self):
        self.seen: list = []

    async def decide(self, ctx):
        for target in ("session", "context"):
            for frag in ctx.input_fragments.get(target, []):
                self.seen.append((frag.role, frag.content))
        return Action(finish=True)


def _run(loop, reason, goal, session_id=None, input_nodes=None):
    return asyncio.run(loop.run({"goal": goal}, session_id=session_id))


class TestSessionPlugin:
    def test_session_injects_history(self):
        """session 插件作为输入节点：注入会话历史消息序列。"""
        bus = EventBus()
        backend = session_cap.DefaultSessionStore()
        backend.append_message("s1", "user", "我叫小明")
        backend.append_message("s1", "assistant", "你好，小明")
        session_cap.register(bus, backend=backend)

        reason = CaptureReason()
        loop = AgentLoop(bus, reason, input_nodes=[{"target": "session", "slot": "user"}])
        _run(loop, reason, "继续", session_id="s1")

        roles = [r for r, _ in reason.seen]
        texts = [c for _, c in reason.seen]
        assert "user" in roles and "assistant" in roles, f"应注入历史：{reason.seen}"
        assert any("我叫小明" in t for t in texts), f"应回灌 user 历史：{texts}"
        assert any("你好，小明" in t for t in texts), f"应回灌 assistant 历史：{texts}"

    def test_session_empty_history_degrades(self):
        """无历史时 session 插件降级为空序列，不报错。"""
        bus = EventBus()
        session_cap.register(bus)  # 默认内存后端
        reason = CaptureReason()
        loop = AgentLoop(bus, reason, input_nodes=[{"target": "session", "slot": "user"}])
        ctx = _run(loop, reason, "你好", session_id="s-none")
        assert ctx.done, "任务应完成"
        assert reason.seen == [], f"无历史时不应有注入消息：{reason.seen}"

    def test_context_reads_from_session_backend(self):
        """解耦：context 从 session_backend 取历史，不再直接碰 store。"""
        bus = EventBus()
        backend = session_cap.DefaultSessionStore()
        backend.append_message("s2", "user", "前文")
        context_cap.register(bus, backend=backend)

        reason = CaptureReason()
        loop = AgentLoop(bus, reason, input_nodes=[{"target": "context", "slot": "user"}])
        _run(loop, reason, "继续", session_id="s2")

        texts = [c for _, c in reason.seen]
        assert any("前文" in t for t in texts), f"context 应从 session_backend 取历史：{texts}"

    def test_custom_backend_injectable(self):
        """自定义存储可注入（任意组装），实现 SessionStore 契约即可。"""
        class CustomBackend(SessionStore):
            def __init__(self):
                self._h = {"s3": [{"role": "user", "content": "自定义后端历史"}]}

            def get_history(self, session_id):
                return self._h.get(session_id, [])

            def append_message(self, session_id, role, content):
                self._h.setdefault(session_id, []).append({"role": role, "content": content})

            def save_session(self, session):
                pass

            def get_agent_session(self, session_id):
                return None

            def save_agent_session(self, session):
                pass

            def list_agent_sessions(self, chat_id):
                return []

            def delete_agent_session(self, session_id):
                pass

        bus = EventBus()
        session_cap.register(bus, backend=CustomBackend())
        reason = CaptureReason()
        loop = AgentLoop(bus, reason, input_nodes=[{"target": "session", "slot": "user"}])
        _run(loop, reason, "继续", session_id="s3")

        texts = [c for _, c in reason.seen]
        assert any("自定义后端历史" in t for t in texts), f"自定义后端应生效：{texts}"

    def test_backend_failure_broadcast_observable(self):
        """存储抛异常时：降级为空历史不阻塞，且广播 session.store_error 可观测。"""
        class BoomBackend(SessionStore):
            def get_history(self, session_id):
                raise RuntimeError("db down")

            def append_message(self, session_id, role, content):
                raise RuntimeError("db down")

            def save_session(self, session):
                raise RuntimeError("db down")

            def get_agent_session(self, session_id):
                raise RuntimeError("db down")

            def save_agent_session(self, session):
                raise RuntimeError("db down")

            def list_agent_sessions(self, chat_id):
                raise RuntimeError("db down")

            def delete_agent_session(self, session_id):
                raise RuntimeError("db down")

        bus = EventBus()
        session_cap.register(bus, backend=BoomBackend())
        errors: list = []

        async def _on_err(n: Notice):
            errors.append(n.payload)

        bus.subscribe("session.store_error", _on_err)
        reason = CaptureReason()
        loop = AgentLoop(bus, reason, input_nodes=[{"target": "session", "slot": "user"}])
        ctx = _run(loop, reason, "你好", session_id="s-boom")

        assert ctx.done, "后端异常不应阻塞任务"
        assert reason.seen == [], f"后端异常应降级为空历史：{reason.seen}"
        assert errors, f"应广播 session.store_error：{errors}"
        assert "db down" in str(errors[0].get("error", "")), f"应含异常信息：{errors}"
