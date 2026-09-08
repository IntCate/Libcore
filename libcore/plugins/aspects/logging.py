"""横切面插件：日志（全匹配，记录每条进出总线的信号）。

信号层日志：能力调用（Dispatch）与系统广播（Notice）都流经总线，
由本 aspect 在 before/after 统一记录，插件自身无需写任何 log。

**定位：过程日志（process log）**，与审计（audit）严格分工：
- 日志记"**过程**"：调用链、参数摘要、耗时、中间态，供排障诊断；
- 审计记"**结果**"：谁、何时、做了什么、结果，供合规追责；
- 日志允许截断 / 采样，审计必须完整且防篡改。

before 记录"目标 + 操作 + 工具/技能名 + 参数摘要"，after 记录执行结果概要
与耗时，从而能仅凭日志还原内核每一步在调度谁、用什么参数、得到什么结果。

输出端走标准 logging（logger 名 ``libcore.aspect``），可配 handler
输出到 console / 文件 / 轮转 / JSON。与总线 ledger（生命周期层）
共用同一套 logging 体系，但各自独立 logger。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Dispatch, CapabilityResult, Notice

logger = logging.getLogger("libcore.aspect")

# 参数摘要长度上限（字符），避免超大 payload 撑爆日志
_MAX_PARAM_LEN = 200


def _clip(v: str) -> str:
    if len(v) <= _MAX_PARAM_LEN:
        return v
    return v[:_MAX_PARAM_LEN] + f"...(+{len(v) - _MAX_PARAM_LEN} chars)"


def _serialize(v: Any) -> str:
    try:
        return json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(v)


def _summarize(d: Dispatch) -> str:
    """从 Dispatch payload 提取可读调度摘要（目标 + 操作 + 具体工具/技能 + 参数）。

    三种门面各自收敛的参数结构：
    - tools: payload 含 ``name``（工具名）、``tool_op``（操作）、``args``（参数）
    - skill: payload 含 ``skill``（技能名）、``op``（find/read/resource/exec）、
             ``script``/``args`` 等
    - 其余   target：直接拼 op + payload 摘要
    """
    p = d.payload or {}
    op = d.op or str(p.get("op") or "")

    # 具体工具 / 技能名（渐进披露门面的"二级目标"）
    sub = p.get("name") or p.get("skill") or p.get("tool_op") or ""
    if sub and isinstance(sub, (str, int)):
        sub = str(sub)

    # 参数摘要：优先抓 args（工具真正入参）或 tool_op 入参
    params: Any = p.get("args") if "args" in p else {k: v for k, v in p.items() if k not in ("name", "skill", "tool_op", "op")}
    if not params:
        params = {}

    parts = [f"target={d.target}"]
    if op:
        parts.append(f"op={op}")
    if sub:
        parts.append(f"sub={sub}")
    if params:
        parts.append(f"params={_clip(_serialize(params))}")
    return " ".join(parts)


class LoggingAspect(Aspect):
    """管道日志：记录每条进出总线的信号（含目标、操作、参数、结果、耗时）。"""

    def __init__(self):
        self._start: dict[str, float] = {}

    def _kind(self, signal) -> str:
        return "dispatch" if isinstance(signal, Dispatch) else "notice"

    def _name(self, signal) -> str:
        return signal.target if isinstance(signal, Dispatch) else signal.topic

    async def before(self, signal):
        self._start[signal.cid.value] = time.perf_counter()
        kind = self._kind(signal)
        if isinstance(signal, Dispatch):
            detail = _summarize(signal)
            logger.info("before %-8s %s cid=%s", kind, detail, signal.cid.value[:8])
        else:
            logger.info("before %-8s %s cid=%s",
                        kind, self._name(signal), signal.cid.value[:8])

    async def after(self, signal, result):
        elapsed = time.perf_counter() - self._start.pop(signal.cid.value, time.perf_counter())
        delta_ms = round(elapsed * 1000, 2)
        kind = self._kind(signal)
        if isinstance(signal, Dispatch):
            ok = result.ok if isinstance(result, CapabilityResult) else True
            # 结果概要：ok + 返回数据的主要键（或错误信息）+ 耗时
            if isinstance(result, CapabilityResult) and result.ok and result.data:
                data_keys = ",".join(str(k) for k in result.data.keys())
                tail = f"ok keys={data_keys} {delta_ms}ms"
            elif isinstance(result, CapabilityResult) and not result.ok:
                tail = f"fail err={_clip(_serialize(result.error) or '')} {delta_ms}ms"
            else:
                tail = f"{'ok' if ok else 'fail'} {delta_ms}ms"
            logger.info("after  %-8s %s -> %s", kind,
                        _summarize(signal), tail)
        else:
            ok = True
            if isinstance(result, CapabilityResult):
                ok = result.ok
            logger.info("after  %-8s %s -> %s %sms", kind,
                        self._name(signal), "ok" if ok else "fail", delta_ms)


def register(bus) -> None:
    bus.add_aspect(LoggingAspect())
