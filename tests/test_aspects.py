"""横切面测试：trace_recorder 真实耗时 + loop_governor 错误判定。

bus.publish() 是异步的，用 asyncio.run() 包裹（不依赖 pytest-asyncio）。
"""
from __future__ import annotations

import asyncio
import time

from libcore.kernel.bus import EventBus, Notice, CapabilityResult
from libcore.kernel.agent.spi import Action
from libcore.plugins.aspects.trace_recorder import TraceRecorderAspect
from libcore.plugins.aspects.loop_governor import LoopGovernorAspect


def _publish(bus, n):
    return asyncio.run(bus.publish(n))


def _ctx():
    from libcore.kernel.bus import Scope
    return Scope(goal="demo")


class TestTraceRecorderElapsed:
    def test_elapsed_ms_recorded(self):
        """loop.iteration 后经 loop.result 结算，elapsed_ms 应为真实非零耗时。"""
        aspect = TraceRecorderAspect()
        bus = EventBus()
        bus.add_aspect(aspect)
        ctx = _ctx()
        action = Action(target="tools", op="run", payload={"name": "bash"})
        _publish(bus, Notice(topic="loop.iteration", payload={"ctx": ctx, "action": action}))
        time.sleep(0.01)
        _publish(bus, Notice(topic="loop.result", payload={
            "ctx": ctx, "action": action,
            "result": CapabilityResult(ok=True, data={"ok": True}),
        }))
        rec = aspect.snapshot()[-1]
        assert rec["elapsed_ms"] > 0, f"elapsed_ms 应为真实耗时，实际 {rec['elapsed_ms']}"


class TestLoopGovernorError:
    def test_error_result_breaks_doom_loop(self):
        """同一工具+参数但结果有错误 → 不判定为死循环（error=True 打破规则）。"""
        aspect = LoopGovernorAspect(threshold=3)
        bus = EventBus()
        bus.add_aspect(aspect)
        ctx = _ctx()
        action = Action(target="tools", op="run", payload={"name": "bash"})
        for _ in range(3):
            _publish(bus, Notice(topic="loop.iteration", payload={"ctx": ctx, "action": action}))
            _publish(bus, Notice(topic="loop.result", payload={
                "ctx": ctx, "action": action,
                "result": CapabilityResult(ok=False, error="boom"),
            }))
        assert not ctx.done, "有错误的重复调用不应触发死循环治理"

    def test_success_repeat_triggers_doom_loop(self):
        """同一工具+参数且无错误连续达阈值 → 触发死循环治理（ctx.done=True）。"""
        aspect = LoopGovernorAspect(threshold=3)
        bus = EventBus()
        bus.add_aspect(aspect)
        ctx = _ctx()
        action = Action(target="tools", op="run", payload={"name": "bash"})
        for _ in range(3):
            _publish(bus, Notice(topic="loop.iteration", payload={"ctx": ctx, "action": action}))
            _publish(bus, Notice(topic="loop.result", payload={
                "ctx": ctx, "action": action,
                "result": CapabilityResult(ok=True, data={"ok": True}),
            }))
        assert ctx.done, "无错误的重复调用应触发死循环治理"
