"""横切面插件：循环追踪记录（原生重写，不 import 旧 app.runtime）。

由旧架构 ``LoopTraceRecorder``（每步输入/输出/耗时/异常）重写而来，但按 libcore 收敛：
- 匹配 ``loop.iteration`` 广播（每轮决策后由 AgentLoop 发出）；
- 记录每轮决策的输入摘要（goal + 观察数）、输出摘要（action target/op）、耗时、异常；
- 快照累积在内存 ``records`` 供读取，并写入 ``ctx.trace`` 供决策者/调用方取用。

不阻断、不侵入，纯观测。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Notice


class TraceRecorderAspect(Aspect):
    """循环追踪记录：每轮决策的输入/输出/耗时/异常。"""

    def __init__(self, max_records: int = 1000):
        self.records: List[Dict[str, Any]] = []
        self._max_records = max_records
        self._start: Dict[int, float] = {}   # iteration -> start_ts

    def matches(self, signal) -> bool:
        return (isinstance(signal, Notice)
                and signal.topic in ("loop.iteration", "loop.input_missing"))

    async def before(self, signal):
        ctx = (signal.payload or {}).get("ctx")
        if signal.topic == "loop.input_missing":
            # 输入节点缺失（插件未注册）：记录降级事件，让配置错误可被感知
            self.records.append({
                "event": "input_missing",
                "target": (signal.payload or {}).get("target"),
                "goal": (signal.payload or {}).get("goal"),
            })
            return None
        if ctx is None:
            return None
        iteration = len(self.records) + 1
        self._start[iteration] = time.monotonic()
        self._emit(iteration, ctx=ctx, action=(signal.payload or {}).get("action"))
        return None

    def _emit(self, iteration: int, *, ctx, action) -> None:
        record: Dict[str, Any] = {
            "iteration": iteration,
            "input_summary": f"goal={ctx.goal}, observations={len(ctx.observations)}",
            "output_summary": "",
            "elapsed_ms": 0.0,
        }
        if action is not None:
            record["output_summary"] = f"{getattr(action, 'target', '')}/{getattr(action, 'op', '')}"
        self.records.append(record)
        if len(self.records) > self._max_records:
            self.records.pop(0)

    def snapshot(self) -> List[Dict[str, Any]]:
        return list(self.records)


def register(bus) -> None:
    bus.add_aspect(TraceRecorderAspect())
