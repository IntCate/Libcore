"""横切面插件：审计（原生重写，不 import 旧 app.runtime.harness）。

由旧架构 ToolAudit（pointcut=tool.call，AFTER 记录到 JSONL）重写而来：
- after 阶段把每次工具调用的参数 / 结果摘要 / 错误 / 耗时追加到 JSONL 审计文件；
- 审计是对"事后轨迹"的留存，不阻断、不侵入各能力插件。
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Dispatch
from libcore.plugins.aspects.sandbox import is_exec_signal

DEFAULT_AUDIT_PATH = "libcore_audit.jsonl"  # 可替换（相对 cwd）


class AuditAspect(Aspect):
    def __init__(self, path: str = DEFAULT_AUDIT_PATH):
        self.path = path
        self._start: dict[str, float] = {}

    def matches(self, signal) -> bool:
        # 审计"执行类"信号：tools run / skill exec（读操作 find/read 不记审计）
        return is_exec_signal(signal)

    async def before(self, signal):
        self._start[signal.target + signal.cid.value] = time.perf_counter()

    async def after(self, signal, result):
        key = signal.target + signal.cid.value
        elapsed = time.perf_counter() - self._start.pop(key, time.perf_counter())
        ok = result is not None and getattr(result, "ok", False)
        record = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "target": signal.target,
            "op": signal.op,
            "delta_ms": round(elapsed * 1000, 2),
            "ok": ok,
            "error": (getattr(result, "error", None) or None) if not ok else None,
        }
        path = self.path if not self.path.startswith("/") else self.path
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def register(bus) -> None:
    bus.add_aspect(AuditAspect())