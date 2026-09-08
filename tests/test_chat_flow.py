"""聊天链路四场景测试：无插件 / 仅 prompt / 仅 context / prompt+context。

验证点（对应验收目标）：
1. 无 prompt/context 插件：一问一答，内核降级（input_missing 广播），日志/审计对得上；
2. 仅 prompt：发给模型的数据 = 1 个 system（prompt 注入）；
3. 仅 context：多轮对话，历史回灌；
4. prompt + context：发给模型的数据 = system + user 两个消息。

同时验证数据流：消息从哪进、走到哪、格式是什么、每一步怎么被处理。
不依赖 pytest-asyncio：用 asyncio.run() 在同步测试函数内跑异步闭环。
"""
from __future__ import annotations

import asyncio
import json
import logging

from libcore.kernel.bus import EventBus, Dispatch, CapabilityResult, Notice
from libcore.kernel.agent import AgentLoop, ReasonProvider, Action
from libcore.plugins.capabilities import context as context_cap
from libcore.plugins.capabilities import prompt as prompt_cap
from libcore.plugins.capabilities import session as session_cap
from libcore.plugins.aspects.audit import AuditAspect
from libcore.plugins.aspects import telemetry as tele_cap
from libcore.plugins.aspects import tracing as trace_cap
from libcore.plugins.aspects import logging as logging_cap


class CaptureReason(ReasonProvider):
    """决策者：捕获注入的 input_fragments（system/user 消息），然后 finish。

    用于验证 prompt/context 是否真的把消息注入给了决策者，以及格式。
    """

    def __init__(self):
        self.seen: list = []  # [(role, content), ...] 按注入顺序
        self.seen_goal: str = ""

    async def decide(self, ctx):
        # 按配置的 input_nodes 顺序读取：prompt 在前（system），context 在后（user）
        for target in ("prompt", "context"):
            for frag in ctx.input_fragments.get(target, []):
                self.seen.append((frag.role, frag.content))
        self.seen_goal = str(ctx.goal.get("goal", "")) if isinstance(ctx.goal, dict) else str(ctx.goal)
        return Action(finish=True)


class _Ledger:
    """收集总线生命周期记账（bus.on / dispatch.no_handler / aspect.error 等）。"""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict):
        self.events.append(event)


def _build_bus(tmp_path, *, with_prompt=False, with_context=False, backend=None):
    """装配总线 + 可选 prompt/context 能力 + 横切面（audit/telemetry/tracing/logging）。"""
    bus = EventBus()
    append = None
    if with_prompt:
        prompt_cap.register(bus, segment=lambda d: "你是测试助手。\n风格：简洁。")
    if with_context:
        append = context_cap.register(bus, backend=backend)
    # 横切面
    audit_path = tmp_path / "chat_audit.jsonl"
    audit = AuditAspect(path=str(audit_path))
    bus.add_aspect(audit)
    tele = tele_cap.TelemetryAspect()
    bus.add_aspect(tele)
    tracing = trace_cap.TracingAspect()
    bus.add_aspect(tracing)
    logging_cap.register(bus)
    return {"bus": bus, "audit": audit, "tele": tele, "tracing": tracing,
            "audit_path": audit_path, "append": append}


def _run_chat(env, reason, goal, session_id=None, input_nodes=None):
    """跑一次对话，返回 Scope。"""
    loop = AgentLoop(env["bus"], reason, input_nodes=input_nodes)
    return asyncio.run(loop.run({"goal": goal}, session_id=session_id))


class TestNoPlugins:
    """场景 1：无 prompt/context 插件，一问一答，内核降级。"""

    def test_kernel_degrades_gracefully(self, tmp_path):
        """无插件时内核不报错，任务完成，input_missing 广播可观测。"""
        env = _build_bus(tmp_path)
        missing: list = []

        async def _on_missing(n: Notice):
            missing.append(n.payload["target"])

        env["bus"].subscribe("loop.input_missing", _on_missing)
        reason = CaptureReason()
        ctx = _run_chat(env, reason, "你好", input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        assert ctx.done, "任务应完成"
        # 无插件：决策者看不到任何注入消息（降级为最简默认）
        assert reason.seen == [], f"无插件时不应有注入消息：{reason.seen}"
        # input_missing 广播应记录缺失的节点
        assert "context" in missing and "prompt" in missing, f"应广播 input_missing：{missing}"

    def test_audit_and_telemetry_consistent(self, tmp_path):
        """无插件时：audit 无执行记录（无工具调用），telemetry 记录 input_missing 广播。"""
        env = _build_bus(tmp_path)
        reason = CaptureReason()
        _run_chat(env, reason, "你好", input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        # audit：纯聊天无工具调用，不应有执行记录
        if env["audit_path"].exists():
            lines = env["audit_path"].read_text(encoding="utf-8").strip().splitlines()
            assert not lines, f"无工具调用时 audit 不应有记录：{lines}"
        # telemetry：应记录 input_missing 广播（可观测降级）
        summary = env["tele"].summary()
        assert "notice.loop.input_missing" in summary, \
            f"telemetry 应记录 input_missing：{list(summary)}"


class TestPromptOnly:
    """场景 2：仅 prompt 插件，发给模型的数据 = 1 个 system。"""

    def test_prompt_injects_single_system(self, tmp_path):
        """仅 prompt：决策者看到 1 个 system 消息（无 user）。"""
        env = _build_bus(tmp_path, with_prompt=True)
        reason = CaptureReason()
        _run_chat(env, reason, "你好", input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        # 决策者应看到 1 个 system（prompt 注入），无 user（context 未注册）
        assert reason.seen == [("system", "你是测试助手。\n风格：简洁。")], \
            f"仅 prompt 应注入 1 个 system：{reason.seen}"

    def test_prompt_audit_consistent(self, tmp_path):
        """仅 prompt：audit 无执行记录（prompt 是读操作），telemetry 记录 prompt 调用。"""
        env = _build_bus(tmp_path, with_prompt=True)
        reason = CaptureReason()
        _run_chat(env, reason, "你好", input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        # audit：prompt 是读操作，不记
        if env["audit_path"].exists():
            lines = env["audit_path"].read_text(encoding="utf-8").strip().splitlines()
            assert not lines, f"prompt 是读操作，audit 不应有记录：{lines}"
        # telemetry：记录 prompt 调用
        summary = env["tele"].summary()
        assert "dispatch.prompt" in summary, f"telemetry 应记录 prompt：{list(summary)}"


class TestContextOnly:
    """场景 3：仅 context 插件，多轮对话历史回灌。"""

    def test_context_only_has_global_history(self, tmp_path):
        """仅 context（无 session）：context 内置临时历史是纯数组，历史混在一起（不区分会话）。"""
        env = _build_bus(tmp_path, with_context=True)  # 不传 backend
        # 通过 context 返回的 append 写入口写入历史（纯数组，不区分会话）
        env["append"]("user", "全局历史A")
        env["append"]("assistant", "全局回复A")
        reason1 = CaptureReason()
        _run_chat(env, reason1, "我叫小明", session_id="sess-g-1",
                  input_nodes=[{"target": "context", "slot": "user"}])
        texts = [c for _, c in reason1.seen]
        # 纯数组历史：不同 session 也看到同一份历史（不区分会话）
        assert any("全局历史A" in t for t in texts), f"应注入内置历史：{texts}"
        assert any("我叫小明" in c for _, c in reason1.seen), f"应注入目标：{reason1.seen}"

    def test_context_with_session_backend_isolates(self, tmp_path):
        """context + session_backend：历史按会话隔离，不同会话互不污染。"""
        backend = session_cap.DefaultSessionStore()
        env = _build_bus(tmp_path, with_context=True, backend=backend)
        # 会话 A 有历史
        backend.append_message("sess-a", "user", "A的历史")
        # 会话 B 无历史
        reason_a = CaptureReason()
        _run_chat(env, reason_a, "继续", session_id="sess-a",
                  input_nodes=[{"target": "context", "slot": "user"}])
        reason_b = CaptureReason()
        _run_chat(env, reason_b, "继续", session_id="sess-b",
                  input_nodes=[{"target": "context", "slot": "user"}])
        texts_a = [c for _, c in reason_a.seen]
        texts_b = [c for _, c in reason_b.seen]
        assert any("A的历史" in t for t in texts_a), f"会话 A 应看到自己的历史：{texts_a}"
        assert not any("A的历史" in t for t in texts_b), f"会话 B 不应看到 A 的历史：{texts_b}"

    def test_context_append_disabled_with_session(self, tmp_path):
        """方案 A：注入 session_backend 时，context 内部数组清空、append 为 no-op。"""
        backend = session_cap.DefaultSessionStore()
        env = _build_bus(tmp_path, with_context=True, backend=backend)
        # 有 session：append 写入口应为 no-op（历史由 session_backend 管理）
        env["append"]("user", "不应进入内部数组")
        reason = CaptureReason()
        _run_chat(env, reason, "你好", session_id="sess-noop",
                  input_nodes=[{"target": "context", "slot": "user"}])
        texts = [c for _, c in reason.seen]
        # 有 session 时，append 写入的数据不应被注入（内部数组已禁用）
        assert not any("不应进入内部数组" in t for t in texts), \
            f"有 session 时 append 应为 no-op：{texts}"
        # 但目标仍正常注入
        assert any("你好" in t for t in texts), f"目标应正常注入：{texts}"

    def test_multi_turn_history_reinjected(self, tmp_path):
        """仅 context：第 1 轮历史持久化，第 2 轮回灌给决策者。"""
        backend = session_cap.DefaultSessionStore()
        env = _build_bus(tmp_path, with_context=True, backend=backend)
        session_id = "sess-ctx-1"

        # 第 1 轮
        reason1 = CaptureReason()
        _run_chat(env, reason1, "我叫小明", session_id=session_id,
                  input_nodes=[{"target": "context", "slot": "user"}])

        # 写回历史（外部装配层持久化）
        backend.append_message(session_id, "user", "我叫小明")
        backend.append_message(session_id, "assistant", "你好，小明")

        # 第 2 轮
        reason2 = CaptureReason()
        _run_chat(env, reason2, "我叫什么？", session_id=session_id,
                  input_nodes=[{"target": "context", "slot": "user"}])

        # 决策者应看到历史（user + assistant）+ 当前目标
        roles = [r for r, _ in reason2.seen]
        texts = [c for _, c in reason2.seen]
        assert "user" in roles and "assistant" in roles, f"应回灌 user+assistant：{reason2.seen}"
        assert any("我叫小明" in t for t in texts), f"应回灌前文：{texts}"
        assert any("你好，小明" in t for t in texts), f"应回灌 assistant 回复：{texts}"

    def test_context_audit_consistent(self, tmp_path):
        """仅 context：audit 无执行记录（context 是读操作），telemetry 记录 context 调用。"""
        backend = session_cap.DefaultSessionStore()
        env = _build_bus(tmp_path, with_context=True, backend=backend)
        reason = CaptureReason()
        _run_chat(env, reason, "你好", session_id="sess-ctx-2",
                  input_nodes=[{"target": "context", "slot": "user"}])
        # audit：context 是读操作，不记
        if env["audit_path"].exists():
            lines = env["audit_path"].read_text(encoding="utf-8").strip().splitlines()
            assert not lines, f"context 是读操作，audit 不应有记录：{lines}"
        # telemetry：记录 context 调用
        summary = env["tele"].summary()
        assert "dispatch.context" in summary, f"telemetry 应记录 context：{list(summary)}"


class TestGoalNormalization:
    """goal 规范化：dict 包裹时提取真实文本，不出现 {'goal': ...} 嵌套冗余。"""

    def test_goal_dict_normalized_to_clean_text(self, tmp_path):
        """context 注入的 user 消息应含干净目标文本，而非 {'goal': ...} 嵌套。"""
        backend = session_cap.DefaultSessionStore()
        env = _build_bus(tmp_path, with_context=True, backend=backend)
        reason = CaptureReason()
        _run_chat(env, reason, "今天天气如何", session_id="sess-goal-1",
                  input_nodes=[{"target": "context", "slot": "user"}])
        # 决策者看到的 user 消息应含干净目标，不应含 {'goal': ...} 嵌套
        texts = [c for _, c in reason.seen]
        assert any("今天天气如何" in t for t in texts), f"应含干净目标：{texts}"
        assert not any("{'goal'" in t for t in texts), f"不应含 dict 嵌套：{texts}"

    def test_goal_string_passthrough(self, tmp_path):
        """goal 为纯字符串时原样拼接，不破坏。"""
        backend = session_cap.DefaultSessionStore()
        bus = EventBus()
        context_cap.register(bus, backend=backend)
        reason = CaptureReason()
        loop = AgentLoop(bus, reason, input_nodes=[{"target": "context", "slot": "user"}])
        # 直接传字符串 goal（模拟外部装配层传纯文本）
        asyncio.run(loop.run("直接字符串目标", session_id="sess-goal-2"))
        texts = [c for _, c in reason.seen]
        assert any("直接字符串目标" in t for t in texts), f"字符串 goal 应原样拼接：{texts}"
        assert not any("{'goal'" in t for t in texts), f"不应含 dict 嵌套：{texts}"


class TestContextStoreFailure:
    """缺口2：context 存储异常应可观测（广播），而非静默吞错。"""

    def test_store_failure_broadcast_observable(self, tmp_path):
        """session_backend 抛异常时：降级不阻塞，且广播 context.store_error 可观测。"""
        class BoomBackend:
            def get_history(self, session_id):
                raise RuntimeError("db down")

        bus = EventBus()
        context_cap.register(bus, backend=BoomBackend())
        errors: list = []

        async def _on_err(n: Notice):
            errors.append(n.payload)

        bus.subscribe("context.store_error", _on_err)
        reason = CaptureReason()
        loop = AgentLoop(bus, reason, input_nodes=[{"target": "context", "slot": "user"}])
        ctx = asyncio.run(loop.run({"goal": "你好"}, session_id="sess-boom"))
        # 降级不阻塞：任务完成，决策者仍收到目标
        assert ctx.done, "store 异常不应阻塞任务"
        texts = [c for _, c in reason.seen]
        assert any("你好" in t for t in texts), f"应降级注入目标：{texts}"
        # 可观测：广播 context.store_error
        assert errors, f"应广播 context.store_error：{errors}"
        assert "db down" in str(errors[0].get("error", "")), f"应含异常信息：{errors}"


class TestPromptAndContext:
    """场景 4：prompt + context 一起，发给模型的数据 = system + user 两个消息。"""

    def test_both_injected_system_and_user(self, tmp_path):
        """prompt+context：决策者看到 system（prompt）+ user（context 目标）。"""
        backend = session_cap.DefaultSessionStore()
        env = _build_bus(tmp_path, with_prompt=True, with_context=True, backend=backend)
        reason = CaptureReason()
        _run_chat(env, reason, "今天天气如何", session_id="sess-both-1", input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        # 决策者应看到 system（prompt）+ user（context 目标）
        roles = [r for r, _ in reason.seen]
        assert "system" in roles, f"应注入 system：{reason.seen}"
        assert "user" in roles, f"应注入 user：{reason.seen}"
        # 顺序：prompt 在前（system），context 在后（user）
        assert reason.seen[0][0] == "system", f"system 应在前：{reason.seen}"
        assert reason.seen[-1][0] == "user", f"user 应在后：{reason.seen}"
        # user 内容应含当前目标
        assert "今天天气如何" in reason.seen[-1][1], f"user 应含目标：{reason.seen[-1]}"

    def test_both_multi_turn_history(self, tmp_path):
        """prompt+context 多轮：system + 历史(user/assistant) + 当前目标。"""
        backend = session_cap.DefaultSessionStore()
        env = _build_bus(tmp_path, with_prompt=True, with_context=True, backend=backend)
        session_id = "sess-both-2"

        # 预置历史
        backend.append_message(session_id, "user", "我叫小明")
        backend.append_message(session_id, "assistant", "你好，小明")

        reason = CaptureReason()
        _run_chat(env, reason, "我叫什么？", session_id=session_id, input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        # 决策者应看到：system（prompt）+ user 历史 + assistant 历史 + user 当前目标
        roles = [r for r, _ in reason.seen]
        texts = [c for _, c in reason.seen]
        assert roles[0] == "system", f"system 应最先：{reason.seen}"
        assert any("我叫小明" in t for t in texts), f"应回灌历史：{texts}"
        assert any("你好，小明" in t for t in texts), f"应回灌 assistant：{texts}"
        assert any("我叫什么？" in t for t in texts), f"应含当前目标：{texts}"

    def test_both_audit_telemetry_consistent(self, tmp_path):
        """prompt+context：audit 无执行记录，telemetry 记录 prompt+context 调用。"""
        backend = session_cap.DefaultSessionStore()
        env = _build_bus(tmp_path, with_prompt=True, with_context=True, backend=backend)
        reason = CaptureReason()
        _run_chat(env, reason, "你好", session_id="sess-both-3", input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        # audit：读操作不记
        if env["audit_path"].exists():
            lines = env["audit_path"].read_text(encoding="utf-8").strip().splitlines()
            assert not lines, f"读操作 audit 不应有记录：{lines}"
        # telemetry：记录 prompt + context
        summary = env["tele"].summary()
        assert "dispatch.prompt" in summary and "dispatch.context" in summary, \
            f"telemetry 应记录 prompt+context：{list(summary)}"
