"""日志可追溯性测试：验证 logging 横切面记录完整、能还原调用链。

现有覆盖盲区：audit/telemetry/tracing 都有专门测试，但 **logging 本身**没有
验证 before/after 是否成对、异常/阻断路径是否留痕、能否凭日志还原调用链。

验证点（多样性）：
1. 正常 dispatch：before/after 成对，含 target/op/sub/params/耗时；
2. handler 抛异常：after 仍执行（记 fail），before/after 成对不缺失；
3. publish 被 before 阻断：after 仍执行（清理残留），成对；
4. cid 调用链：同一 dispatch 的 before/after 用同一 cid，可串联；
5. 审计与日志分工：审计只记执行类（tools），日志记所有信号（含读操作 context/prompt）；
6. 多信号类型：tools / skill / mcp 三种执行类信号，审计 action_type 各异；
7. 多结果路径：成功 / 失败 / 被 permission 阻断 / handler 异常，日志与审计都留痕；
8. Notice 广播：日志记录广播（before/after 成对），审计不记广播。

用 caplog 捕获 ``libcore.aspect`` logger（logging 横切面输出端）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from libcore.kernel.bus import EventBus, Dispatch, CapabilityResult, Notice, Aspect
from libcore.plugins.aspects import logging as logging_cap
from libcore.plugins.aspects import audit as audit_cap
from libcore.plugins.aspects import permission as perm_cap
from libcore.plugins.capabilities import context as context_cap
from libcore.plugins.capabilities import prompt as prompt_cap
from libcore.plugins.capabilities import session as session_cap


def _dispatch(bus, target, op, payload):
    return asyncio.run(bus.dispatch(Dispatch(target=target, op=op, payload=payload)))


def _publish(bus, topic, payload):
    return asyncio.run(bus.publish(Notice(topic=topic, payload=payload)))


def _aspect_lines(caplog):
    """提取 libcore.aspect logger 的日志行。"""
    return [r.getMessage() for r in caplog.records
            if r.name == "libcore.aspect"]


def _audit_records(path):
    """读取审计文件为记录列表。"""
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").strip().splitlines() if l.strip()]


class TestLoggingPairing:
    """核查 1：正常 dispatch 的 before/after 成对，可还原调用链。"""

    def test_dispatch_before_after_paired(self, caplog):
        with caplog.at_level(logging.INFO, logger="libcore.aspect"):
            bus = EventBus()
            logging_cap.register(bus)
            bus.on("tools", lambda d: CapabilityResult(ok=True, data={"stdout": "hi"}))

            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "echo hi"}})

        lines = _aspect_lines(caplog)
        # 应有 before + after 两条
        before = [l for l in lines if l.startswith("before")]
        after = [l for l in lines if l.startswith("after")]
        assert len(before) == 1, f"应有 1 条 before：{before}"
        assert len(after) == 1, f"应有 1 条 after：{after}"
        # before 应含 target/op/sub/params（可还原"调度谁、用什么参数"）
        assert "target=tools" in before[0], f"before 应含 target：{before[0]}"
        assert "op=run" in before[0], f"before 应含 op：{before[0]}"
        assert "sub=bash" in before[0], f"before 应含 sub：{before[0]}"
        assert "echo hi" in before[0], f"before 应含参数：{before[0]}"
        # after 应含结果概要 + 耗时
        assert "ok" in after[0], f"after 应含结果：{after[0]}"
        assert "ms" in after[0], f"after 应含耗时：{after[0]}"

    def test_cid_links_before_after(self, caplog):
        """同一 dispatch 的 before/after 用同一 cid，可串联调用链。"""
        with caplog.at_level(logging.INFO, logger="libcore.aspect"):
            bus = EventBus()
            logging_cap.register(bus)
            bus.on("tools", lambda d: CapabilityResult(ok=True, data={"ok": True}))

            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "echo a"}})

        lines = _aspect_lines(caplog)
        before = [l for l in lines if l.startswith("before")][0]
        after = [l for l in lines if l.startswith("after")][0]
        # 提取 cid（格式：cid=xxxxxxxx）
        import re
        b_cid = re.search(r"cid=(\w+)", before).group(1)
        a_cid = re.search(r"cid=(\w+)", after).group(1)
        assert b_cid == a_cid, f"before/after 应同 cid：{b_cid} vs {a_cid}"


class TestLoggingExceptionPath:
    """核查 2：handler 抛异常时，after 仍执行（记 fail），before/after 成对。"""

    def test_handler_exception_still_paired(self, caplog):
        with caplog.at_level(logging.INFO, logger="libcore.aspect"):
            bus = EventBus()
            logging_cap.register(bus)

            def boom(d: Dispatch) -> CapabilityResult:
                raise RuntimeError("handler 内部爆炸")

            bus.on("tools", boom)
            try:
                _dispatch(bus, "tools", "run",
                          {"name": "bash", "tool_op": "exec", "args": {"command": "echo x"}})
            except RuntimeError:
                pass

        lines = _aspect_lines(caplog)
        before = [l for l in lines if l.startswith("before")]
        after = [l for l in lines if l.startswith("after")]
        # 异常路径：before/after 仍成对（#13 修复：handler 异常也走 after）
        assert len(before) == 1, f"异常路径应有 1 条 before：{before}"
        assert len(after) == 1, f"异常路径应有 1 条 after：{after}"
        # after 应记 fail + 错误信息
        assert "fail" in after[0], f"异常路径 after 应记 fail：{after[0]}"
        assert "handler 内部爆炸" in after[0], f"after 应含错误：{after[0]}"


class TestLoggingPublishBlocked:
    """核查 3：publish 被 before 阻断时，after 仍执行（清理残留），成对。"""

    def test_publish_blocked_still_paired(self, caplog):
        with caplog.at_level(logging.INFO, logger="libcore.aspect"):
            bus = EventBus()
            logging_cap.register(bus)

            class Blocker(Aspect):
                def matches(self, signal):
                    return isinstance(signal, Notice) and signal.topic == "blocked"

                async def before(self, signal):
                    return CapabilityResult(ok=False, error="阻断广播")

            bus.add_aspect(Blocker())
            _publish(bus, "blocked", {"x": 1})

        lines = _aspect_lines(caplog)
        before = [l for l in lines if l.startswith("before")]
        after = [l for l in lines if l.startswith("after")]
        # 阻断路径：before/after 仍成对（#14 修复：阻断也走 after）
        assert len(before) == 1, f"阻断路径应有 1 条 before：{before}"
        assert len(after) == 1, f"阻断路径应有 1 条 after：{after}"
        assert "blocked" in before[0], f"before 应含 topic：{before[0]}"


class TestLoggingAuditDivision:
    """核查 4：日志与审计分工——日志记所有信号，审计只记执行类。"""

    def test_logging_records_read_ops_audit_does_not(self, caplog, tmp_path):
        """context/prompt 是读操作：日志记录，审计不记。"""
        with caplog.at_level(logging.INFO, logger="libcore.aspect"):
            bus = EventBus()
            logging_cap.register(bus)
            audit_path = tmp_path / "audit.jsonl"
            bus.add_aspect(audit_cap.AuditAspect(path=str(audit_path)))
            # 读操作能力：context + prompt
            backend = session_cap.DefaultSessionStore()
            context_cap.register(bus, backend=backend)
            prompt_cap.register(bus, segment=lambda d: "你是助手。")

            _dispatch(bus, "context", "run", {"goal": "你好", "session_id": "s1"})
            _dispatch(bus, "prompt", "run", {"goal": "你好"})

        lines = _aspect_lines(caplog)
        # 日志：应记录 context + prompt 两个 dispatch（读操作也记）
        assert any("target=context" in l for l in lines), f"日志应记 context：{lines}"
        assert any("target=prompt" in l for l in lines), f"日志应记 prompt：{lines}"
        # 审计：读操作不记（文件不存在或为空）
        if audit_path.exists():
            content = audit_path.read_text(encoding="utf-8").strip()
            assert not content, f"读操作审计不应有记录：{content}"


class TestLoggingMultiSignalTypes:
    """核查 5：多信号类型——tools / skill / mcp 三种执行类信号，审计 action_type 各异。"""

    def test_three_exec_signal_types_audited(self, tmp_path):
        """tools run / skill exec / mcp run 三种执行类信号，审计 action_type 各异。"""
        bus = EventBus()
        logging_cap.register(bus)
        audit_path = tmp_path / "audit.jsonl"
        bus.add_aspect(audit_cap.AuditAspect(path=str(audit_path)))
        # 三种执行类 handler
        bus.on("tools", lambda d: CapabilityResult(ok=True, data={"stdout": "ok"}))
        bus.on("skill", lambda d: CapabilityResult(ok=True, data={"ok": True}))
        bus.on("mcp", lambda d: CapabilityResult(ok=True, data={"ok": True}))

        _dispatch(bus, "tools", "run", {"name": "bash", "tool_op": "exec", "args": {"command": "echo a"}})
        _dispatch(bus, "skill", "exec", {"skill": "devops/deploy", "script": "scripts/gen.py", "args": {"name": "web"}})
        _dispatch(bus, "mcp", "run", {"name": "calculator", "tool_op": "add", "args": {"a": 1, "b": 2}})

        records = _audit_records(audit_path)
        # 三种执行类信号全部被审计，action_type 各异
        assert len(records) == 3, f"应审计 3 条：{records}"
        types = {r["action_type"] for r in records}
        assert types == {"tool_call", "skill_exec", "mcp_run"}, f"action_type 应各异：{types}"
        # 资源名正确
        by_type = {r["action_type"]: r for r in records}
        assert by_type["tool_call"]["resource"] == "bash"
        assert by_type["skill_exec"]["resource"] == "devops/deploy"
        assert by_type["mcp_run"]["resource"] == "calculator"

    def test_three_signal_types_logged(self, caplog):
        """三种执行类信号都被日志记录（含 sub 二级目标）。"""
        with caplog.at_level(logging.INFO, logger="libcore.aspect"):
            bus = EventBus()
            logging_cap.register(bus)
            bus.on("tools", lambda d: CapabilityResult(ok=True, data={"ok": True}))
            bus.on("skill", lambda d: CapabilityResult(ok=True, data={"ok": True}))
            bus.on("mcp", lambda d: CapabilityResult(ok=True, data={"ok": True}))

            _dispatch(bus, "tools", "run", {"name": "bash", "tool_op": "exec", "args": {"command": "echo a"}})
            _dispatch(bus, "skill", "exec", {"skill": "devops/deploy", "script": "scripts/gen.py", "args": {"name": "web"}})
            _dispatch(bus, "mcp", "run", {"name": "calculator", "tool_op": "add", "args": {"a": 1, "b": 2}})

        lines = _aspect_lines(caplog)
        # 三种信号都被日志记录，且 sub 二级目标正确
        assert any("target=tools" in l and "sub=bash" in l for l in lines), f"应记 tools/bash：{lines}"
        assert any("target=skill" in l and "sub=devops/deploy" in l for l in lines), f"应记 skill：{lines}"
        assert any("target=mcp" in l and "sub=calculator" in l for l in lines), f"应记 mcp：{lines}"


class TestLoggingMultiOutcomes:
    """核查 6：多结果路径——成功 / 失败 / 被 permission 阻断 / handler 异常，日志与审计都留痕。"""

    def test_four_outcomes_all_recorded(self, caplog, tmp_path):
        """成功 / 失败 / 阻断 / 异常四种结果，日志与审计都留痕。"""
        with caplog.at_level(logging.INFO, logger="libcore.aspect"):
            bus = EventBus()
            logging_cap.register(bus)
            audit_path = tmp_path / "audit.jsonl"
            bus.add_aspect(audit_cap.AuditAspect(path=str(audit_path)))
            perm_cap.register(bus)

            def flaky(d: Dispatch) -> CapabilityResult:
                cmd = (d.payload.get("args") or {}).get("command", "")
                if cmd == "boom":
                    raise RuntimeError("handler 爆炸")
                return CapabilityResult(ok=True, data={"stdout": "ok"})

            bus.on("tools", flaky)
            # 1. 成功
            _dispatch(bus, "tools", "run", {"name": "bash", "tool_op": "exec", "args": {"command": "echo ok"}})
            # 2. 失败（handler 返回 ok=False）
            bus.on("tools", lambda d: CapabilityResult(ok=False, error="业务失败"))
            _dispatch(bus, "tools", "run", {"name": "bash", "tool_op": "exec", "args": {"command": "echo fail"}})
            # 3. 被 permission 阻断（危险命令）
            _dispatch(bus, "tools", "run", {"name": "bash", "tool_op": "exec", "args": {"command": "rm -rf /"}})
            # 4. handler 异常
            bus.on("tools", flaky)
            try:
                _dispatch(bus, "tools", "run", {"name": "bash", "tool_op": "exec", "args": {"command": "boom"}})
            except RuntimeError:
                pass

        lines = _aspect_lines(caplog)
        # 日志：4 次 dispatch 都成对（before/after 各 4 条）
        before = [l for l in lines if l.startswith("before")]
        after = [l for l in lines if l.startswith("after")]
        assert len(before) == 4, f"应有 4 条 before：{before}"
        assert len(after) == 4, f"应有 4 条 after：{after}"
        # 日志含成功与失败
        assert any("ok" in l for l in after), f"日志应含成功：{after}"
        assert any("fail" in l for l in after), f"日志应含失败：{after}"
        # 审计：4 次执行全部留痕（含阻断与异常）
        records = _audit_records(audit_path)
        assert len(records) == 4, f"审计应记 4 条：{records}"
        oks = [r["ok"] for r in records]
        assert oks.count(True) == 1, f"应 1 次成功：{oks}"
        assert oks.count(False) == 3, f"应 3 次失败（业务失败/阻断/异常）：{oks}"
        # 阻断与异常的错误信息
        errors = [r["error"] for r in records if not r["ok"]]
        assert any("权限拒绝" in (e or "") for e in errors), f"应含权限拒绝：{errors}"
        assert any("handler 爆炸" in (e or "") for e in errors), f"应含异常：{errors}"


class TestLoggingNoticeBroadcast:
    """核查 7：Notice 广播——日志记录广播（before/after 成对），审计不记广播。"""

    def test_notice_logged_but_not_audited(self, caplog, tmp_path):
        """广播（Notice）被日志记录，但审计不记（审计只记执行类 Dispatch）。"""
        with caplog.at_level(logging.INFO, logger="libcore.aspect"):
            bus = EventBus()
            logging_cap.register(bus)
            audit_path = tmp_path / "audit.jsonl"
            bus.add_aspect(audit_cap.AuditAspect(path=str(audit_path)))

            _publish(bus, "loop.iteration", {"ctx": None, "action": None})
            _publish(bus, "loop.finish", {"goal": "x"})

        lines = _aspect_lines(caplog)
        # 日志：广播被记录（before/after 成对）
        before = [l for l in lines if l.startswith("before")]
        after = [l for l in lines if l.startswith("after")]
        assert len(before) == 2, f"应有 2 条广播 before：{before}"
        assert len(after) == 2, f"应有 2 条广播 after：{after}"
        assert any("loop.iteration" in l for l in lines), f"应记 loop.iteration：{lines}"
        assert any("loop.finish" in l for l in lines), f"应记 loop.finish：{lines}"
        # 审计：广播不记（文件为空）
        assert _audit_records(audit_path) == [], "审计不应记广播"
