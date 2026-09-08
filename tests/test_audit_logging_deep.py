"""深度核查：审计与日志在异常路径 / 广播阻断路径下是否完整。

重点验证总线流转的覆盖盲区：
1. handler 抛异常时，dispatch 的 after 阶段是否执行（审计/日志/遥测是否缺失）；
2. publish 被 before 阻断时，after 是否执行（日志是否只有 before 无 after）；
3. 审计 hash 链跨实例是否断裂（热更新重建横切管场景）。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

from libcore.kernel.bus import EventBus, Dispatch, CapabilityResult, Notice, Aspect
from libcore.plugins.aspects import audit as audit_cap
from libcore.plugins.aspects import logging as logging_cap
from libcore.plugins.aspects import telemetry as telemetry_cap


def _dispatch(bus, target, op, payload):
    return asyncio.run(bus.dispatch(Dispatch(target=target, op=op, payload=payload)))


def _publish(bus, topic, payload):
    return asyncio.run(bus.publish(Notice(topic=topic, payload=payload)))


class TestAuditLoggingExceptionPath:
    """核查 1：handler 抛异常时，审计/日志/遥测是否记录该次执行。"""

    def test_handler_exception_skips_after(self):
        """handler 抛异常 → dispatch 直接抛出，after 不执行 → 审计/遥测缺失。"""
        with tempfile.TemporaryDirectory() as td:
            bus = EventBus()
            audit_path = os.path.join(td, "audit.jsonl")
            bus.add_aspect(audit_cap.AuditAspect(path=audit_path))
            telemetry = telemetry_cap.TelemetryAspect()
            bus.add_aspect(telemetry)

            def boom(d: Dispatch) -> CapabilityResult:
                raise RuntimeError("handler 内部爆炸")

            # 用 tools target（exec 信号），audit 才会匹配记录
            bus.on("tools", boom)
            try:
                _dispatch(bus, "tools", "run", {"name": "bash", "tool_op": "exec",
                                                "args": {"command": "echo x"}})
            except RuntimeError:
                pass
            # 审计：应记录这次执行（即使失败）
            with open(audit_path, encoding="utf-8") as f:
                records = [json.loads(line) for line in f if line.strip()]
            # 遥测：应记录这次 dispatch
            summary = telemetry.summary()
            print(f"[核查1] 审计记录数={len(records)}, 遥测={summary}")
            # 修复后：handler 异常时 after 仍执行，审计/遥测都记录
            assert len(records) == 1, f"handler 异常时审计应记录 1 条，实际 {len(records)}"
            assert records[0]["ok"] is False, "异常执行应审计为失败"
            assert "handler 内部爆炸" in records[0]["error"]
            assert "dispatch.tools" in summary, "handler 异常时遥测应记录"


class TestAuditLoggingPublishBlocked:
    """核查 2：publish 被 before 阻断时，after 是否执行。"""

    def test_publish_blocked_skips_after(self):
        """publish 被 before 阻断 → 直接 return，after 不执行 → 日志只有 before 无 after。"""
        bus = EventBus()
        logging_cap.register(bus)
        telemetry = telemetry_cap.TelemetryAspect()
        bus.add_aspect(telemetry)

        class Blocker(Aspect):
            def matches(self, signal):
                return isinstance(signal, Notice) and signal.topic == "blocked"

            async def before(self, signal):
                return CapabilityResult(ok=False, error="阻断广播")

        bus.add_aspect(Blocker())
        _publish(bus, "blocked", {"x": 1})
        summary = telemetry.summary()
        print(f"[核查2] 遥测={summary}")
        # 修复后：阻断的广播也走 after，telemetry 记录该广播（无 _start 残留）
        assert "notice.blocked" in summary, "阻断广播时遥测应记录"
        assert summary["notice.blocked"]["calls"] == 1, "阻断广播应计 1 次"
        # _start 无残留
        assert len(telemetry._start) == 0, f"阻断广播后 _start 不应残留：{telemetry._start}"


class TestAuditHashChainAcrossInstances:
    """核查 3：审计 hash 链跨实例是否断裂（热更新重建横切管场景）。"""

    def test_hash_chain_breaks_across_instances(self):
        """两个 AuditAspect 实例写同一文件 → 第二个实例 seq 从 0、prev_hash 从全零 → 链断裂。"""
        with tempfile.TemporaryDirectory() as td:
            audit_path = os.path.join(td, "audit.jsonl")
            # 实例 1：写 1 条
            bus1 = EventBus()
            bus1.add_aspect(audit_cap.AuditAspect(path=audit_path))
            bus1.on("tools", lambda d: CapabilityResult(ok=True, data={"ok": True}))
            _dispatch(bus1, "tools", "run", {"name": "bash", "tool_op": "exec",
                                             "args": {"command": "echo a"}})
            # 实例 2（模拟热更新重建）：写 1 条
            bus2 = EventBus()
            bus2.add_aspect(audit_cap.AuditAspect(path=audit_path))
            bus2.on("tools", lambda d: CapabilityResult(ok=True, data={"ok": True}))
            _dispatch(bus2, "tools", "run", {"name": "bash", "tool_op": "exec",
                                             "args": {"command": "echo b"}})
            with open(audit_path, encoding="utf-8") as f:
                records = [json.loads(line) for line in f if line.strip()]
            # 链完整性：第 2 条的 prev_hash 应 == 第 1 条的 hash
            print(f"[核查3] 记录数={len(records)}, rec1.hash={records[0]['hash'][:8]}, "
                  f"rec2.prev_hash={records[1]['prev_hash'][:8]}")
            # 修复后：跨实例续链，第 2 条 prev_hash == 第 1 条 hash，seq 连续
            assert records[1]["prev_hash"] == records[0]["hash"], \
                "跨实例 hash 链应连续（续链修复）"
            assert records[1]["seq"] == records[0]["seq"] + 1, "seq 应连续递增"
