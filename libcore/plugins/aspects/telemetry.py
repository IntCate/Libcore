"""横切面插件：遥测（原生重写，不 import 旧 app.runtime.harness）。

由旧架构 SystemTelemetry（全局 Span / 指标 / 错误事件）重写而来：
- 覆盖所有流经总线的信号（Dispatch + Notice），在最外层记录起止耗时与 ok 指标；
- 结果仅累积在内存 spans 供读取，不阻断、不侵入。
"""
from __future__ import annotations

import time

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Dispatch, CapabilityResult

CONS = "libcore"  # 仅作标识，不引入旧遥测后端


class TelemetryAspect(Aspect):
    def __init__(self):
        self.spans: list[dict] = []
        self._start: dict[str, float] = {}

    def matches(self, signal) -> bool:
        return True  # 全局遥测：包裹所有信号

    def _key(self, signal) -> str:
        return signal.cid.value

    async def before(self, signal):
        self._start[self._key(signal)] = time.perf_counter()

    async def after(self, signal, result):
        elapsed = time.perf_counter() - self._start.pop(self._key(signal), time.perf_counter())
        kind = "dispatch" if isinstance(signal, Dispatch) else "notice"
        ok = result.ok if isinstance(result, CapabilityResult) else True
        self.spans.append({
            "kind": kind,
            "name": signal.target if isinstance(signal, Dispatch) else signal.topic,
            "delta_ms": round(elapsed * 1000, 2),
            "ok": ok,
        })


def register(bus) -> None:
    bus.add_aspect(TelemetryAspect())