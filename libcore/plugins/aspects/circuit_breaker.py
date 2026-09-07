"""横切面插件：熔断护栏（原生重写，不 import 旧 app.runtime.harness）。

由旧架构 ToolCircuitBreaker（pointcut=tool.call，CLOSED/OPEN/HALF_OPEN）重写而来，
但按 libcore 简化：
- 每 target 独立维护失败计数与熔断状态；
- before：若已熔断（OPEN）直接拒绝，不让 handler 执行；
- after：成功则复位计数，失败则累加，连续失败超阈值切换为 OPEN。
- 无 HALF_OPEN 自动探活，保持最小（护栏只需"别让持续失败打穿下游"）。
"""
from __future__ import annotations

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Dispatch, CapabilityResult

# 连续失败多少次即熔断
FAILURE_THRESHOLD = 3


class CircuitBreakerAspect(Aspect):
    def __init__(self, threshold: int = FAILURE_THRESHOLD):
        self.threshold = threshold
        self._fails: dict[str, int] = {}
        self._open: set[str] = set()

    def matches(self, signal) -> bool:
        # 工具 / MCP 工具经对应门面执行（run op）才走熔断；读操作与技能脚本不在此护栏范围
        return (
            isinstance(signal, Dispatch)
            and signal.target in ("tools", "mcp")
            and (signal.op or "") == "run"
        )

    @staticmethod
    def _tool_name(signal) -> str:
        # 熔断按具体工具（payload.name）独立跟踪，而非整个 tools 门面
        payload = getattr(signal, "payload", None) or {}
        name = payload.get("name")
        return str(name) if name else "tools"

    def _record_failure(self, target: str) -> int:
        n = self._fails.get(target, 0) + 1
        self._fails[target] = n
        if n >= self.threshold:
            self._open.add(target)
        return n

    async def before(self, signal):
        name = self._tool_name(signal)
        if name in self._open:
            return CapabilityResult(
                ok=False,
                error=f"熔断中：{name} 连续失败超阈值，已暂停",
                data={"tool": name, "state": "OPEN"},
            )
        return None

    async def after(self, signal, result):
        if not isinstance(signal, Dispatch):
            return
        name = self._tool_name(signal)
        if result is not None and getattr(result, "ok", False):
            self._fails.pop(name, None)   # 成功复位
            self._open.discard(name)      # 恢复后可再次尝试
        else:
            n = self._record_failure(name)
            if n >= self.threshold:
                self._open.add(name)


def register(bus, threshold: int = FAILURE_THRESHOLD) -> None:
    """注册熔断护栏。``threshold`` 可由 aspects.yaml 的 ``config`` 覆盖（缺省 3）。"""
    bus.add_aspect(CircuitBreakerAspect(threshold=threshold))