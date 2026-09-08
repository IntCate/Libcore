"""总线健壮性测试：aspect 自身异常 / publish 订阅者异常时，after 阶段仍执行。

验证主张（"系统运行不漏"）：
1. aspect 的 before 抛异常时，after 阶段（审计/遥测）仍应执行，不留漏记；
2. publish 的订阅者抛异常时，不中断后续订阅者，且 after 阶段仍执行；
3. 异常仍向上传播（调用方可见），但观测不缺失。
"""
from __future__ import annotations

import asyncio

from libcore.kernel.bus import EventBus, Dispatch, Notice, Aspect, CapabilityResult


class BoomAspect(Aspect):
    """before 阶段抛异常的横切面。"""

    def matches(self, signal) -> bool:
        return True

    async def before(self, signal):
        raise RuntimeError("aspect before boom")


class AfterRecorder(Aspect):
    """记录 after 是否被调用。"""

    def __init__(self):
        self.after_called = False
        self.after_signals = []

    def matches(self, signal) -> bool:
        return True

    async def after(self, signal, result):
        self.after_called = True
        self.after_signals.append(signal)


def test_aspect_exception_still_runs_after():
    """aspect 自身 before 抛异常时，after 阶段仍执行（审计/遥测不漏记）。"""
    bus = EventBus()
    bus.add_aspect(BoomAspect())
    recorder = AfterRecorder()
    bus.add_aspect(recorder)

    def handler(d):
        return CapabilityResult(ok=True, data={"x": 1})

    bus.on("t", handler)

    async def run():
        try:
            await bus.dispatch(Dispatch(target="t", op="run"))
        except RuntimeError:
            pass
        assert recorder.after_called, "aspect 异常后 after 仍应执行"

    asyncio.run(run())


def test_publish_subscriber_exception_does_not_skip_others_or_after():
    """publish 订阅者抛异常时，不中断后续订阅者，且 after 仍执行。"""
    bus = EventBus()
    recorder = AfterRecorder()
    bus.add_aspect(recorder)

    calls = []

    def sub1(n):
        calls.append("sub1")
        raise RuntimeError("sub1 boom")

    def sub2(n):
        calls.append("sub2")

    bus.subscribe("t", sub1)
    bus.subscribe("t", sub2)

    async def run():
        try:
            await bus.publish(Notice(topic="t", payload={}))
        except RuntimeError:
            pass
        assert calls == ["sub1", "sub2"], f"后续订阅者不应被中断：{calls}"
        assert recorder.after_called, "publish 订阅者异常后 after 仍应执行"

    asyncio.run(run())
