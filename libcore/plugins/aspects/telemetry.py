"""横切面插件：遥测（原生重写，不 import 旧 app.runtime.harness）。

由旧架构 SystemTelemetry（全局 Span / 指标 / 错误事件）重写而来，
并按 Agent Harness 工程观测栈重新定位：

- 遥测只做**指标（metric）**：耗时、成功率、计数，供趋势 / 告警；
- 与审计（audit）严格分工：审计记"谁做了什么"（合规留痕），
  遥测记"多快 / 多成功"（性能指标），互不重叠；
- 结果仅累积在内存 spans 供读取，不阻断、不侵入。
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Dict

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Dispatch, CapabilityResult

CONS = "libcore"  # 仅作标识，不引入旧遥测后端


class TelemetryAspect(Aspect):
    """指标遥测：记录每个信号的耗时与 ok，并聚合计数 / 成功率。"""

    def __init__(self):
        self.spans: list[dict] = []
        self._start: dict[str, float] = {}
        self._counts: Counter = Counter()      # (kind, name) -> 调用次数
        self._errors: Counter = Counter()      # (kind, name) -> 失败次数
        self._total_ms: Dict[str, float] = {}  # (kind, name) -> 累计耗时

    def matches(self, signal) -> bool:
        return True  # 全局遥测：包裹所有信号

    def _key(self, signal) -> str:
        return signal.cid.value

    def _name(self, signal) -> str:
        return signal.target if isinstance(signal, Dispatch) else signal.topic

    async def before(self, signal):
        self._start[self._key(signal)] = time.perf_counter()

    async def after(self, signal, result):
        elapsed = time.perf_counter() - self._start.pop(self._key(signal), time.perf_counter())
        kind = "dispatch" if isinstance(signal, Dispatch) else "notice"
        name = self._name(signal)
        ok = result.ok if isinstance(result, CapabilityResult) else True
        delta_ms = round(elapsed * 1000, 2)

        # 单次 span（可下钻到 trace）
        self.spans.append({
            "kind": kind,
            "name": name,
            "cid": signal.cid.value,
            "delta_ms": delta_ms,
            "ok": ok,
        })

        # 聚合指标（趋势 / 告警）
        agg_key = (kind, name)
        self._counts[agg_key] += 1
        if not ok:
            self._errors[agg_key] += 1
        self._total_ms[agg_key] = self._total_ms.get(agg_key, 0.0) + delta_ms

    def summary(self) -> dict:
        """导出聚合指标：计数 / 失败 / 平均耗时 / 成功率。"""
        out = {}
        for (kind, name), total in self._counts.items():
            errs = self._errors.get((kind, name), 0)
            ms = self._total_ms.get((kind, name), 0.0)
            out[f"{kind}.{name}"] = {
                "calls": total,
                "errors": errs,
                "avg_ms": round(ms / total, 2) if total else 0.0,
                "success_rate": round((total - errs) / total, 4) if total else 1.0,
            }
        return out


def register(bus) -> None:
    bus.add_aspect(TelemetryAspect())
