"""重试护栏测试：RetryAspect 在 before 阶段接管执行 + 指数退避重试。

验证：
1. 配置 retries 后，失败自动重试，重试成功则返回成功结果给调用方；
2. 重试耗尽仍失败，返回最后一次失败结果；
3. 未配置 retries（默认 0）时不接管，执行走原 handler。
"""
from __future__ import annotations

import asyncio

from libcore.kernel import EventBus
from libcore.kernel.bus import CapabilityResult, Dispatch
from libcore.plugins.aspects.retry import RetryAspect


def test_retry_succeeds_after_transient_failure():
    """失败后重试成功，调用方拿到成功结果。"""
    calls = {"n": 0}

    def flaky_handler(d: Dispatch) -> CapabilityResult:
        calls["n"] += 1
        if calls["n"] < 3:
            return CapabilityResult(ok=False, error="暂时失败")
        return CapabilityResult(ok=True, data={"ok": True})

    bus = EventBus()
    bus.on("flaky", flaky_handler)
    bus.add_aspect(RetryAspect(retries=2, retry_delay=0.01, bus=bus))

    async def run():
        res = await bus.dispatch(Dispatch(target="flaky", op="run", payload={}))
        assert res.ok is True
        assert calls["n"] == 3  # 1 次原始 + 2 次重试

    asyncio.run(run())


def test_retry_exhausted_returns_last_failure():
    """重试耗尽仍失败，返回最后一次失败结果。"""
    calls = {"n": 0}

    def always_fail(d: Dispatch) -> CapabilityResult:
        calls["n"] += 1
        return CapabilityResult(ok=False, error="持续失败")

    bus = EventBus()
    bus.on("flaky", always_fail)
    bus.add_aspect(RetryAspect(retries=2, retry_delay=0.01, bus=bus))

    async def run():
        res = await bus.dispatch(Dispatch(target="flaky", op="run", payload={}))
        assert res.ok is False
        assert res.error == "持续失败"
        assert calls["n"] == 3  # 1 次原始 + 2 次重试

    asyncio.run(run())


def test_retry_disabled_passes_through():
    """未配置 retries（默认 0）时不接管，执行走原 handler（只调用 1 次）。"""
    calls = {"n": 0}

    def fail_handler(d: Dispatch) -> CapabilityResult:
        calls["n"] += 1
        return CapabilityResult(ok=False, error="失败")

    bus = EventBus()
    bus.on("flaky", fail_handler)
    bus.add_aspect(RetryAspect(retries=0, bus=bus))

    async def run():
        res = await bus.dispatch(Dispatch(target="flaky", op="run", payload={}))
        assert res.ok is False
        assert calls["n"] == 1  # 不重试

    asyncio.run(run())


def test_retry_handles_handler_exception():
    """handler 抛异常（而非返回失败 result）时，重试仍生效。"""
    calls = {"n": 0}

    def throwing_handler(d: Dispatch) -> CapabilityResult:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("暂时性异常")
        return CapabilityResult(ok=True, data={"ok": True})

    bus = EventBus()
    bus.on("flaky", throwing_handler)
    bus.add_aspect(RetryAspect(retries=2, retry_delay=0.01, bus=bus))

    async def run():
        res = await bus.dispatch(Dispatch(target="flaky", op="run", payload={}))
        assert res.ok is True
        assert calls["n"] == 3  # 1 次原始 + 2 次重试

    asyncio.run(run())


def test_retry_exception_exhausted_returns_failure():
    """handler 持续抛异常时，重试耗尽返回失败结果（不向上抛）。"""
    calls = {"n": 0}

    def always_throw(d: Dispatch) -> CapabilityResult:
        calls["n"] += 1
        raise RuntimeError("持续异常")

    bus = EventBus()
    bus.on("flaky", always_throw)
    bus.add_aspect(RetryAspect(retries=2, retry_delay=0.01, bus=bus))

    async def run():
        res = await bus.dispatch(Dispatch(target="flaky", op="run", payload={}))
        assert res.ok is False
        assert "持续异常" in res.error
        assert calls["n"] == 3  # 1 次原始 + 2 次重试

    asyncio.run(run())
