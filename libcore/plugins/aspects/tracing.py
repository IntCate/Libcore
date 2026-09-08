"""横切面插件：追踪（全匹配，把每步调度累积成自迭代训练数据）。

由旧架构 TraceRecorder 重写而来，并按 Agent Harness 工程观测栈补全：

- 记录每步调度的 **span**：谁（cid）→ 调谁（target/op）→ 结果（ok/data），
  并带 ``parent_cid`` 构成**父子链路**，可还原 agent 完整执行轨迹；
- 记录每步**耗时**（delta_ms），供回放 / 训练 / 性能分析；
- 结果仅累积在内存 records 供读取，不阻断、不侵入。
"""
from __future__ import annotations

import time

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Dispatch, CapabilityResult


class TracingAspect(Aspect):
    """追踪：把每步调度（谁→调谁→结果→耗时）累积成自迭代训练数据。"""

    def __init__(self):
        self.records: list[dict] = []
        self._start: dict[str, float] = {}

    def matches(self, signal) -> bool:
        return isinstance(signal, Dispatch)  # 只追踪点名调度，广播不追踪

    async def before(self, signal):
        self._start[signal.cid.value] = time.perf_counter()

    async def after(self, signal, result):
        elapsed = time.perf_counter() - self._start.pop(signal.cid.value, time.perf_counter())
        self.records.append({
            "target": signal.target,
            "op": signal.op,
            "cid": signal.cid.value,
            "parent_cid": signal.parent_cid.value if signal.parent_cid else None,
            "delta_ms": round(elapsed * 1000, 2),
            "ok": result.ok if isinstance(result, CapabilityResult) else True,
            "data": result.data if isinstance(result, CapabilityResult) else result,
        })


def register(bus) -> None:
    bus.add_aspect(TracingAspect())
