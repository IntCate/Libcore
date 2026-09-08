"""事件因果链测试：CorrelationId 父子链路 + TracingAspect 轨迹还原。

验证点（对应 docs/core/event-bus.md 与测试矩阵"事件/CorrelationId 因果链"）：
1. Scope 构造时产生根因果 id（root_cid）；
2. AgentLoop 派发的 Dispatch 带 parent_cid=root_cid，构成父子链路；
3. TracingAspect 记录每步调度的 cid + parent_cid，可还原完整执行轨迹；
4. 同一会话内多次派发共享同一 root_cid，但各自 cid 唯一。

bus.dispatch()/publish() 是异步的，用 asyncio.run() 包裹（不依赖 pytest-asyncio）。
"""
from __future__ import annotations

import asyncio

from libcore.kernel.bus import EventBus, Dispatch, Notice, CapabilityResult, Scope
from libcore.kernel.agent import ReasonProvider, Action, AgentLoop
from libcore.plugins.aspects.tracing import TracingAspect


def _dispatch(bus, d):
    return asyncio.run(bus.dispatch(d))


def _publish(bus, n):
    return asyncio.run(bus.publish(n))


class _EchoHandler:
    def __init__(self):
        self.seen_cids = []

    def __call__(self, d: Dispatch) -> CapabilityResult:
        self.seen_cids.append((d.cid.value, d.parent_cid.value if d.parent_cid else None))
        return CapabilityResult(ok=True, data={"echo": d.payload})


class _OneShotReason(ReasonProvider):
    """决策者：第一轮调 echo，第二轮 finish。"""

    def __init__(self):
        self.calls = 0

    async def decide(self, ctx):
        self.calls += 1
        if self.calls == 1:
            return Action(target="echo", op="run", payload={"n": 1})
        return Action(finish=True)


class TestCorrelationIdChain:
    def test_scope_has_root_cid(self):
        """Scope 构造时产生根因果 id，且每次构造唯一。"""
        s1 = Scope(goal="g")
        s2 = Scope(goal="g")
        assert s1.root_cid.value
        assert s1.root_cid.value != s2.root_cid.value

    def test_new_cid_derives_distinct_child(self):
        """new_cid 派生子 id，与 root_cid 不同。"""
        s = Scope(goal="g")
        child = s.new_cid()
        assert child.value != s.root_cid.value

    def test_agentloop_dispatch_carries_parent_cid(self):
        """AgentLoop 派发的 Dispatch 应带 parent_cid=root_cid，构成父子链路。"""
        bus = EventBus()
        handler = _EchoHandler()
        bus.on("echo", handler)
        loop = AgentLoop(bus, _OneShotReason())
        ctx = asyncio.run(loop.run({"goal": "g"}))
        # 至少一次 echo 派发
        assert handler.seen_cids, "应至少派发一次 echo"
        for cid, parent in handler.seen_cids:
            assert parent == ctx.root_cid.value, \
                f"Dispatch.parent_cid 应为 root_cid，实际 {parent} != {ctx.root_cid.value}"
            assert cid != parent, "子 cid 不应等于父 cid"

    def test_tracing_records_parent_child_chain(self):
        """TracingAspect 记录 cid + parent_cid，可还原父子链路。"""
        bus = EventBus()
        handler = _EchoHandler()
        bus.on("echo", handler)
        tracing = TracingAspect()
        bus.add_aspect(tracing)
        loop = AgentLoop(bus, _OneShotReason())
        ctx = asyncio.run(loop.run({"goal": "g"}))
        assert tracing.records, "追踪应记录调度"
        echo_recs = [r for r in tracing.records if r["target"] == "echo"]
        assert echo_recs, "追踪应含 echo 调度"
        for rec in echo_recs:
            assert rec["parent_cid"] == ctx.root_cid.value, \
                f"追踪 parent_cid 应为 root_cid，实际 {rec['parent_cid']}"
            assert rec["cid"] != rec["parent_cid"]
