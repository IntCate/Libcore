"""横切面插件：重试护栏（before 接管执行 + 指数退避重试）。

生产就绪缺口：工具调用失败时自动重试。经架构核实，重试**不能**放在 after 阶段
（after 返回值被总线丢弃，无法回传重试结果），也不能放门面层（不统一、易被改坏）。
正确做法是**在 before 阶段接管执行**（与 sandbox 同模式）：
- 匹配执行类信号（tools/mcp run、skill exec）；
- 用 ``bus.invoke`` 直接调用原 handler（不走横切面，避免递归）；
- 失败则指数退避重试，成功或耗尽后把结果返回给调用方（before 返回值被总线短路回传）。

``retries`` 由 aspects.yaml 的 ``config`` 注入（缺省 0 = 不接管，保持最小）。
"""
from __future__ import annotations

import asyncio

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Dispatch, CapabilityResult


class RetryAspect(Aspect):
    """重试护栏：before 接管执行，失败指数退避重试，返回最终结果。"""

    def __init__(self, retries: int = 0, retry_delay: float = 0.1, bus=None):
        self.retries = retries
        self.retry_delay = retry_delay
        self._bus = bus

    def matches(self, signal) -> bool:
        # 重试是通用投递策略：匹配所有点名调度（Dispatch），不限于 exec 信号
        return isinstance(signal, Dispatch)

    async def before(self, signal):
        if self.retries <= 0 or self._bus is None:
            return None  # 未配置重试 -> 不接管，执行走原 handler
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                result = await self._bus.invoke(signal)
            except Exception as e:  # noqa: BLE001 - handler 抛异常也视为一次失败，纳入重试
                last_error = e
                result = None
            if result is not None and getattr(result, "ok", False):
                return result  # 成功 -> 返回给调用方
            if attempt < self.retries:
                await asyncio.sleep(self.retry_delay * (2 ** attempt))
        # 重试耗尽：返回最后一次失败结果（handler 抛异常则归一为失败 CapabilityResult）
        if result is None:
            return CapabilityResult(ok=False, error=str(last_error or "重试耗尽仍失败"))
        return result


def register(bus, retries: int = 0, retry_delay: float = 0.1) -> None:
    """注册重试护栏。``retries``/``retry_delay`` 可由 aspects.yaml 的 ``config`` 覆盖（缺省 0 = 不接管）。"""
    bus.add_aspect(RetryAspect(retries=retries, retry_delay=retry_delay, bus=bus))
