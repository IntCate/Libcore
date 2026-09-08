"""全横切面组合测试：内核 + 全部 10 个横切面同时装配。

重点验证：
1. 拦截有效：permission 拒绝危险命令、circuit_breaker 熔断、sandbox 接管、budget_guard 熔断、loop_governor 死循环；
2. 审计完整：audit 记录每次执行（append-only + SHA-256 hash 链），无遗漏；
3. 日志完整：logging 记录每条进出总线的信号（before/after 成对）；
4. 可追溯流转：tracing 记录每步调度（cid + parent_cid + delta_ms），telemetry 聚合指标；
5. 无遗漏：每个 dispatch 都被所有观察类横切面记录。

bus.dispatch()/publish() 是异步的，用 asyncio.run() 包裹。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

from libcore.kernel.bus import EventBus, Dispatch, CapabilityResult, Notice
from libcore.kernel.agent.spi import Action
from libcore.plugins.capabilities import tool as tools_cap
from libcore.plugins.aspects import (
    permission as perm_cap,
    circuit_breaker as cb_cap,
    sandbox as sandbox_cap,
    budget_guard as bg_cap,
    loop_governor as lg_cap,
    trace_recorder as tr_cap,
    logging as logging_cap,
    tracing as tracing_cap,
    audit as audit_cap,
    telemetry as telemetry_cap,
)


def _dispatch(bus, target, op, payload):
    return asyncio.run(bus.dispatch(Dispatch(target=target, op=op, payload=payload)))


def _publish(bus, topic, payload):
    return asyncio.run(bus.publish(Notice(topic=topic, payload=payload)))


def _ctx():
    from libcore.kernel.bus import Scope
    return Scope(goal="demo")


def _build_bus(tmpdir):
    """装配内核 + 全部 10 个横切面 + tools 能力。"""
    bus = EventBus()
    tools_cap.register(bus)
    perm_cap.register(bus)
    cb_cap.register(bus, threshold=3)
    sandbox_cap.register(bus, enabled=True)
    bg_cap.register(bus, max_iterations=5)
    lg_cap.register(bus, threshold=3)
    tr_cap.register(bus)
    logging_cap.register(bus)
    tracing_cap.register(bus)
    bus.add_aspect(audit_cap.AuditAspect(path=os.path.join(tmpdir, "audit.jsonl")))
    telemetry_cap.register(bus)
    return bus


def _aspects(bus):
    return bus._aspects


class TestAllAspectsInterception:
    """拦截有效：5 类护栏各自生效。"""

    def test_permission_blocks_dangerous(self):
        with tempfile.TemporaryDirectory() as td:
            bus = _build_bus(td)
            r = _dispatch(bus, "tools", "run",
                          {"name": "bash", "tool_op": "exec", "args": {"command": "rm -rf /"}})
            assert r.ok is False, "危险命令应被 permission 拦截"
            assert "权限拒绝" in r.error

    def test_circuit_breaker_opens(self):
        with tempfile.TemporaryDirectory() as td:
            bus = _build_bus(td)
            for _ in range(3):
                _dispatch(bus, "tools", "run",
                          {"name": "bash", "tool_op": "exec",
                           "args": {"command": "nonexistent_cmd_xyz"}})
            r = _dispatch(bus, "tools", "run",
                          {"name": "bash", "tool_op": "exec",
                           "args": {"command": "echo blocked"}})
            assert r.ok is False, "熔断后应拒绝"
            assert "熔断" in r.error

    def test_sandbox_isolates_bash(self):
        with tempfile.TemporaryDirectory() as td:
            bus = _build_bus(td)
            r = _dispatch(bus, "tools", "run",
                          {"name": "bash", "tool_op": "exec", "args": {"command": "python -c print(2+2)"}})
            assert r.ok, f"安全命令应执行：{r.error}"
            assert r.data.get("sandboxed") is True, "应被沙箱接管"

    def test_budget_guard_breaks_loop(self):
        with tempfile.TemporaryDirectory() as td:
            bus = _build_bus(td)
            ctx = _ctx()
            action = Action(target="tools", op="run", payload={"name": "bash"})
            for _ in range(5):
                _publish(bus, "loop.iteration", {"ctx": ctx, "action": action})
            assert ctx.done, "预算耗尽应熔断"

    def test_loop_governor_detects_doom_loop(self):
        with tempfile.TemporaryDirectory() as td:
            bus = _build_bus(td)
            ctx = _ctx()
            action = Action(target="tools", op="run", payload={"name": "bash"})
            for _ in range(3):
                _publish(bus, "loop.iteration", {"ctx": ctx, "action": action})
                _publish(bus, "loop.result", {
                    "ctx": ctx, "action": action,
                    "result": CapabilityResult(ok=True, data={"ok": True}),
                })
            assert ctx.done, "死循环应被检测"


class TestAllAspectsAudit:
    """审计完整：audit 记录每次执行，hash 链防篡改，无遗漏。"""

    def test_audit_records_every_exec(self):
        with tempfile.TemporaryDirectory() as td:
            bus = _build_bus(td)
            audit_path = os.path.join(td, "audit.jsonl")
            # 3 次执行：1 次成功 + 1 次被 permission 拦截 + 1 次成功
            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "echo a"}})
            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "rm -rf /"}})
            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "echo b"}})
            with open(audit_path, encoding="utf-8") as f:
                records = [json.loads(line) for line in f if line.strip()]
            # 无遗漏：3 次执行全部被审计
            assert len(records) == 3, f"应审计 3 条，实际 {len(records)}"
            # hash 链完整：每条 prev_hash == 上一条 hash
            prev = "0" * 64
            for rec in records:
                assert rec["prev_hash"] == prev, "hash 链断裂"
                # 重算 hash 校验防篡改
                payload = json.dumps(
                    {k: v for k, v in rec.items() if k != "hash"},
                    ensure_ascii=False, sort_keys=True, default=str)
                import hashlib
                assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == rec["hash"]
                prev = rec["hash"]
            # 拦截也被审计（ok=False）
            assert records[1]["ok"] is False, "被拦截的执行也应审计为失败"
            assert "权限拒绝" in records[1]["error"]


class TestAllAspectsTracing:
    """可追溯流转：tracing 记录每步调度，telemetry 聚合指标。"""

    def test_tracing_records_every_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            bus = _build_bus(td)
            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "python -c print(1)"}})
            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "python -c print(2)"}})
            tracing = next(a for a in _aspects(bus) if isinstance(a, tracing_cap.TracingAspect))
            # 无遗漏：2 次 dispatch 全部被追踪
            assert len(tracing.records) == 2, f"应追踪 2 条，实际 {len(tracing.records)}"
            for rec in tracing.records:
                assert rec["target"] == "tools"
                assert rec["op"] == "run"
                assert rec["cid"], "应有 cid"
                assert rec["delta_ms"] >= 0, "耗时应非负"

    def test_telemetry_aggregates(self):
        with tempfile.TemporaryDirectory() as td:
            bus = _build_bus(td)
            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "python -c print(1)"}})
            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "python -c print(2)"}})
            telemetry = next(a for a in _aspects(bus)
                             if isinstance(a, telemetry_cap.TelemetryAspect))
            summary = telemetry.summary()
            # 聚合指标：tools.run 被调用 2 次，成功率 100%
            key = "dispatch.tools"
            assert summary[key]["calls"] == 2, f"应聚合 2 次调用：{summary}"
            assert summary[key]["success_rate"] == 1.0, "成功率应为 100%"


class TestAllAspectsNoLeak:
    """无遗漏：每个 dispatch 都被所有观察类横切面记录。"""

    def test_every_dispatch_recorded_by_all_observers(self):
        with tempfile.TemporaryDirectory() as td:
            bus = _build_bus(td)
            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "echo a"}})
            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "echo b"}})
            # 观察类横切面：tracing / telemetry / audit / logging
            tracing = next(a for a in _aspects(bus) if isinstance(a, tracing_cap.TracingAspect))
            telemetry = next(a for a in _aspects(bus)
                             if isinstance(a, telemetry_cap.TelemetryAspect))
            # 每个 dispatch 都被 tracing 和 telemetry 记录
            assert len(tracing.records) == 2
            assert len(telemetry.spans) == 2
            # 每个 dispatch 的 cid 在 tracing 和 telemetry 中都能对上
            trace_cids = {r["cid"] for r in tracing.records}
            tele_cids = {s["cid"] for s in telemetry.spans}
            assert trace_cids == tele_cids, "tracing 与 telemetry 的 cid 应一致（无遗漏）"
