"""基本聊天验收测试：单轮对话 + 多轮对话，验证 prompt/context/内核/横切面。

覆盖链路（对应验收目标）：
1. prompt 能力：注入 system 指令（角色/风格/领域知识）；
2. context 能力：注入会话历史（多轮对话回灌前文）；
3. 内核：AgentLoop 完成 reason→dispatch→observe 闭环；
4. 横切面：audit 记录每次对话执行、telemetry 聚合指标；
5. 多轮对话：会话历史持久化 → 回灌 → 决策者能看到前文。

不依赖 pytest-asyncio：用 asyncio.run() 在同步测试函数内跑异步闭环。
"""
from __future__ import annotations

import asyncio
import json

from libcore.kernel.bus import EventBus, Dispatch, CapabilityResult
from libcore.kernel.agent import AgentLoop, ReasonProvider, Action
from libcore.plugins.capabilities import context as context_cap
from libcore.plugins.capabilities import prompt as prompt_cap
from libcore.plugins.capabilities import session as session_cap
from libcore.plugins.aspects.audit import AuditAspect
from libcore.plugins.aspects import telemetry as tele_cap


class ChatReason(ReasonProvider):
    """聊天决策者：读取注入的上下文，记录"看到了什么"，然后 finish。

    用于验证 prompt/context 是否真的把 system 指令和历史注入给了决策者。
    """

    def __init__(self):
        self.seen_system: list = []
        self.seen_history: list = []
        self.seen_goal: str = ""

    async def decide(self, ctx):
        # 读取注入的 prompt（system 指令）
        for frag in ctx.input_fragments.get("prompt", []):
            if frag.role == "system":
                self.seen_system.append(frag.content)
        # 读取注入的 context（历史 + 目标），保留 role 以便区分 user/assistant
        for frag in ctx.input_fragments.get("context", []):
            self.seen_history.append(f"{frag.role}: {frag.content}")
        self.seen_goal = str(ctx.goal.get("goal", "")) if isinstance(ctx.goal, dict) else str(ctx.goal)
        return Action(finish=True)


def _build_env(tmp_path):
    """装配总线 + prompt/context 能力 + 会话后端 + 横切面。"""
    bus = EventBus()
    backend = session_cap.DefaultSessionStore()
    # prompt：注入角色/风格/领域知识片段
    prompt_cap.register(bus, segment=lambda d: "你是测试助手。\n风格：简洁。\n领域：测试。")
    # context：注入会话后端，使 payload 带 session_id 时从后端读历史（解耦存储）
    context_cap.register(bus, backend=backend)
    # 横切面
    audit_path = tmp_path / "chat_audit.jsonl"
    audit = AuditAspect(path=str(audit_path))
    bus.add_aspect(audit)
    tele = tele_cap.TelemetryAspect()
    bus.add_aspect(tele)
    return {"bus": bus, "backend": backend, "audit": audit, "tele": tele,
            "audit_path": audit_path}


def _run_chat(env, reason, goal, session_id=None):
    """跑一次对话，返回 Scope。"""
    loop = AgentLoop(env["bus"], reason, input_nodes=[
        {"target": "context", "slot": "user"},
        {"target": "prompt", "slot": "system"},
    ])
    return asyncio.run(loop.run({"goal": goal}, session_id=session_id))


class TestSingleTurnChat:
    def test_prompt_injected_as_system(self, tmp_path):
        """单轮对话：prompt 能力注入 system 指令给决策者。"""
        env = _build_env(tmp_path)
        reason = ChatReason()
        _run_chat(env, reason, "你好")
        assert reason.seen_system, "决策者应看到 system 指令"
        assert "你是测试助手" in reason.seen_system[0]
        assert "风格：简洁" in reason.seen_system[0]

    def test_context_injects_goal(self, tmp_path):
        """单轮对话：context 能力注入任务目标给决策者。"""
        env = _build_env(tmp_path)
        reason = ChatReason()
        _run_chat(env, reason, "今天天气如何")
        assert reason.seen_goal == "今天天气如何"

    def test_audit_records_chat(self, tmp_path):
        """单轮对话：audit 记录执行类信号（tools/skill/mcp run），输入节点是读操作不记。"""
        env = _build_env(tmp_path)
        reason = ChatReason()
        _run_chat(env, reason, "你好")
        # 纯聊天（无工具调用）时，audit 不应有记录——输入节点是读操作，审计只审执行类信号
        if env["audit_path"].exists():
            lines = env["audit_path"].read_text(encoding="utf-8").strip().splitlines()
            assert not lines, f"纯聊天无工具调用，audit 不应记录输入节点：{lines}"

    def test_telemetry_aggregates_input_nodes(self, tmp_path):
        """单轮对话：telemetry 聚合 prompt/context 输入节点指标。"""
        env = _build_env(tmp_path)
        reason = ChatReason()
        _run_chat(env, reason, "你好")
        summary = env["tele"].summary()
        assert "dispatch.prompt" in summary, f"telemetry 缺 prompt 指标：{list(summary)}"
        assert "dispatch.context" in summary, f"telemetry 缺 context 指标：{list(summary)}"


class TestMultiTurnChat:
    def test_history_persisted_and_reinjected(self, tmp_path):
        """多轮对话：第 1 轮历史持久化，第 2 轮回灌给决策者。"""
        env = _build_env(tmp_path)
        session_id = "sess-chat-1"

        # 第 1 轮：用户说"我叫小明"
        reason1 = ChatReason()
        _run_chat(env, reason1, "我叫小明", session_id=session_id)

        # 手动把第 1 轮对话写回会话后端（模拟外部装配层持久化）
        env["backend"].append_message(session_id, "user", "我叫小明")
        env["backend"].append_message(session_id, "assistant", "你好，小明")

        # 第 2 轮：用户说"我叫什么？"——决策者应看到前文"我叫小明"
        reason2 = ChatReason()
        _run_chat(env, reason2, "我叫什么？", session_id=session_id)

        # 决策者应看到历史（前文"我叫小明" + assistant 回复）
        history_text = "\n".join(reason2.seen_history)
        assert "我叫小明" in history_text, f"第 2 轮应回灌前文，实际：{history_text}"
        assert "你好，小明" in history_text, f"第 2 轮应回灌 assistant 回复，实际：{history_text}"

    def test_history_trimmed_to_window(self, tmp_path):
        """多轮对话：context 裁剪历史到最近 N 条（默认 10）。"""
        env = _build_env(tmp_path)
        session_id = "sess-chat-2"

        # 预置 15 条历史
        for i in range(15):
            env["backend"].append_message(
                session_id,
                "user" if i % 2 == 0 else "assistant",
                f"消息{i}",
            )

        reason = ChatReason()
        _run_chat(env, reason, "继续", session_id=session_id)

        history_text = "\n".join(reason.seen_history)
        # 裁剪后应只保留最近 10 条（不含最后一条 user 时是 9 条）
        assert "消息0" not in history_text, f"最早消息应被裁剪，实际含：{history_text}"
        assert "消息14" in history_text, f"最近消息应保留，实际缺：{history_text}"
