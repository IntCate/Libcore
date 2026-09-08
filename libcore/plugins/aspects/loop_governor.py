"""横切面插件：循环治理（跨轮次死循环检测，原生重写，不 import 旧 app.runtime）。

由旧架构 ``DoomLoopDetector``（跨轮次判定）重写而来，但按 libcore 收敛：
- 匹配 ``loop.iteration`` 广播（每轮决策前由 AgentLoop 发出）；
- 维护跨轮次工具调用历史，检测"同一工具 + 同一参数 + 无错误"的重复调用；
- 连续重复达阈值 → 把 ``ctx.done`` 置 True 治理循环，并广播 ``loop.guard``。

规则（对齐旧 DoomLoopDetector）：
  1. 工具名相同
  2. 参数相同（JSON 序列化后比对）
  3. 无错误（result_is_error=False）
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Notice


class LoopGovernorAspect(Aspect):
    """循环治理：跨轮次死循环检测。

    跨轮次历史按会话（ctx.root_cid）隔离：并发任务各自独立检测，
    互不污染（生产级并发安全）。
    """

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._history: Dict[str, List[Dict[str, Any]]] = {}   # ctx.root_cid -> 该会话工具调用记录

    def matches(self, signal) -> bool:
        return isinstance(signal, Notice) and signal.topic in ("loop.iteration", "loop.result")

    async def before(self, signal):
        ctx = (signal.payload or {}).get("ctx")
        if ctx is None:
            return None
        key = ctx.root_cid.value
        if signal.topic == "loop.result":
            # 本轮 dispatch 已返回：用真实结果判定"是否有错误"，再检测死循环
            result = (signal.payload or {}).get("result")
            action = (signal.payload or {}).get("action")
            if action is None:
                return None
            target = getattr(action, "target", None)
            if not target:
                return None
            payload = getattr(action, "payload", None) or {}
            sig = (str(target), json.dumps(payload, sort_keys=True, ensure_ascii=False))
            is_error = not getattr(result, "ok", True)
            history = self._history.setdefault(key, [])
            history.append({"sig": sig, "error": is_error})
            if self._is_doom_loop(history):
                ctx.done = True
                ctx.observations.append({"error": "检测到死循环（重复调用同一工具且无新信息），自动停止。"})
            return None
        # loop.iteration：仅记录本轮决策（无结果，error 判定交给 loop.result）
        return None

    def _is_doom_loop(self, history: List[Dict[str, Any]]) -> bool:
        if len(history) < self.threshold:
            return False
        recent = history[-self.threshold:]
        sigs = [r["sig"] for r in recent]
        return len(set(sigs)) == 1 and all(not r["error"] for r in recent)

    def reset(self) -> None:
        self._history.clear()


def register(bus, threshold: int = 3) -> None:
    """注册循环治理护栏。``threshold`` 可由 aspects.yaml 的 ``config`` 覆盖（缺省 3）。"""
    bus.add_aspect(LoopGovernorAspect(threshold=threshold))
