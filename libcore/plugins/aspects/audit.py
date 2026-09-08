"""横切面插件：审计（原生重写，不 import 旧 app.runtime.harness）。

由旧架构 ToolAudit（pointcut=tool.call，AFTER 记录到 JSONL）重写而来，
并按 Agent Harness 工程审计标准重新设计：

- 审计是"合规留痕"，不是"过程日志"：只记 **谁（who）、何时（when）、
  做了什么（what）、结果（outcome）**，不记耗时 / 参数细节（那些归
  telemetry / tracing / logging）；
- 只审"执行类"信号（tools run / skill exec / mcp run），只读操作不记；
- **防篡改**：append-only + SHA-256 hash 链。每条记录含 ``prev_hash``，
  篡改任何一条都会导致后续 hash 全部失配，从而可被检测；
- 不阻断、不侵入各能力插件。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Dispatch, CapabilityResult
from libcore.plugins.aspects.sandbox import is_exec_signal

DEFAULT_AUDIT_PATH = "libcore_audit.jsonl"  # 可替换（相对 cwd）

# 动作分类：把"执行类"信号归一为审计语义的动作类型
_ACTION_BY_OP = {
    ("tools", "run"): "tool_call",
    ("skill", "exec"): "skill_exec",
    ("mcp", "run"): "mcp_run",
}


def _action_type(signal: Dispatch) -> str:
    """把 Dispatch 归一为审计动作类型（tool_call / skill_exec / mcp_run）。"""
    return _ACTION_BY_OP.get((signal.target, signal.op), f"{signal.target}.{signal.op}")


def _resource(signal: Dispatch) -> str:
    """审计"作用于什么"：工具 / 技能 / MCP 工具名（payload 里的二级目标）。"""
    p = signal.payload or {}
    return str(p.get("name") or p.get("skill") or p.get("tool_op") or "")


def _subject(signal: Dispatch) -> Dict[str, Optional[str]]:
    """审计"谁"：从 payload 提取 agent / session 主体，缺省用 cid 兜底。"""
    p = signal.payload or {}
    return {
        "agent_id": p.get("agent_id"),
        "session_id": p.get("session_id"),
        "cid": signal.cid.value,
    }


class AuditAspect(Aspect):
    """合规审计：who + when + what + outcome，append-only + SHA-256 hash 链。"""

    def __init__(self, path: str = DEFAULT_AUDIT_PATH):
        self.path = path
        self._seq = 0
        self._prev_hash = "0" * 64  # 链头：全零占位
        self._resume_chain()

    def _resume_chain(self) -> None:
        """从既有审计文件尾部续链：恢复 seq 与 prev_hash，保证跨实例 hash 链不断裂。

        热更新重建横切管（AspectLoader.watch()）会新建 AuditAspect 实例，
        若从全零重新起链，会破坏 append-only 的防篡改保证。这里读取文件
        最后一条记录，续上 seq 与 hash。
        """
        path = self.path if not self.path.startswith("/") else self.path
        if not os.path.exists(path):
            return
        last = None
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        last = json.loads(line)
        except (OSError, ValueError):
            return
        if last is not None:
            self._seq = int(last.get("seq", 0))
            self._prev_hash = str(last.get("hash", "0" * 64))

    def matches(self, signal) -> bool:
        # 审计"执行类"信号：tools run / skill exec / mcp run（读操作不记）
        return is_exec_signal(signal)

    def _hash(self, record: Dict[str, Any]) -> str:
        """对记录做 SHA-256：内容 + 序号 + 前一条 hash，构成防篡改链。"""
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def after(self, signal, result):
        ok = result is not None and getattr(result, "ok", False)
        self._seq += 1
        record = {
            "seq": self._seq,
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "action_type": _action_type(signal),
            "target": signal.target,
            "op": signal.op,
            "resource": _resource(signal),
            "ok": ok,
            "error": (getattr(result, "error", None) or None) if not ok else None,
            "prev_hash": self._prev_hash,
        }
        record.update(_subject(signal))
        record["hash"] = self._hash(record)
        self._prev_hash = record["hash"]

        path = self.path if not self.path.startswith("/") else self.path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def register(bus) -> None:
    bus.add_aspect(AuditAspect())
