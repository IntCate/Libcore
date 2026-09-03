"""横切面插件：追踪（全匹配，把每步调度累积成自迭代训练数据）。"""
from __future__ import annotations

from libcore.core.bus import Aspect
from libcore.core.event import Dispatch


class TracingAspect(Aspect):
    """追踪：把每步调度（谁→调谁→结果）累积成自迭代训练数据。"""

    def __init__(self):
        self.records: list[dict] = []

    async def after(self, signal, result):
        if isinstance(signal, Dispatch):
            self.records.append({
                "target": signal.target, "op": signal.op,
                "cid": signal.cid.value,
                "parent_cid": signal.parent_cid.value if signal.parent_cid else None,
                "ok": result.ok, "data": result.data,
            })


def register(bus) -> None:
    bus.add_aspect(TracingAspect())