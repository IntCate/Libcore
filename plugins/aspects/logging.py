"""横切面插件：日志（全匹配，记录每条进出总线的信号）。"""
from __future__ import annotations

from libcore.core.bus import Aspect
from libcore.core.event import Dispatch, CapabilityResult


class LoggingAspect(Aspect):
    """管道日志：打印每条进出总线的信号（证明横切面包围所有信号）。"""

    def _name(self, signal):
        return signal.target if isinstance(signal, Dispatch) else signal.topic

    async def before(self, signal):
        kind = "dispatch" if isinstance(signal, Dispatch) else "notice"
        print(f"  [aspect] before  {kind:<8} {self._name(signal)}  cid={signal.cid.value[:8]}")

    async def after(self, signal, result):
        kind = "dispatch" if isinstance(signal, Dispatch) else "notice"
        ok = result.ok if isinstance(result, CapabilityResult) else True
        print(f"  [aspect] after   {kind:<8} {self._name(signal)}  -> {'ok' if ok else 'fail'}")


def register(bus) -> None:
    bus.add_aspect(LoggingAspect())