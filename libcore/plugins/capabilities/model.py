"""内置能力插件：model —— agent 调度它选/切模型（agentOS 系统能力）。

语义：agent 接管系统核心后，**模型管理**是 agent 可调度的能力节点。
agent 决定用哪个模型、切换模型、列出可用模型。

产出约定：``data["model"]`` 为当前/选中的模型；``data["models"]`` 为可用模型列表（可选）。
无模型后端 → 降级为 not_implemented（诚实披露，绝不编造模型）。

装配（零耦合，可拆卸）：
- ``register(bus, manager=)`` 可注入模型管理后端（签名 ``manager(op, payload) -> dict``），
  如包装旧 model_manager 或 backends 注册表；
- 本节点不 dispatch 任何其他节点。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from libcore.kernel.bus import Dispatch, CapabilityResult
from libcore.llm import (
    names as _llm_names,
    has as _llm_has,
    create as _llm_create,
)

DESCRIPTION = (
    "系统能力节点：agent 调度我管理模型。决定用哪个模型、切换模型、列出可用模型、创建 backend。"
)

# manager 类型：接收 op + payload，返回模型结果 dict
ModelManager = Callable[[str, Dict[str, Any]], Dict[str, Any]]

# 当前选中模型（简单内存状态，不做持久化）
_current: Optional[str] = None


def _default_manager(op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """默认 manager：通过 libcore.llm 池实现 list / select / switch / create。"""
    global _current
    if op == "list":
        return {"models": _llm_names()}
    if op in ("select", "switch"):
        name = str(payload.get("name") or "")
        if not _llm_has(name):
            raise NotImplementedError(f"未知模型：{name!r}")
        _current = name
        return {"model": name, "models": _llm_names()}
    if op == "create":
        name = str(payload.get("name") or "")
        kwargs = dict(payload.get("kwargs") or {})
        _llm_create(name, **kwargs)  # 实例化校验/创建
        return {"created": name}
    raise NotImplementedError(f"不支持的 model 操作：{op!r}")


def register(bus, manager: Optional[ModelManager] = None) -> None:
    """注册 model 能力节点。``manager`` 可注入模型管理后端；缺省占位降级。"""
    manage = manager or _default_manager

    def handle(d: Dispatch) -> CapabilityResult:
        op = d.op or "list"
        payload = d.payload or {}
        try:
            result = manage(op, payload)
        except NotImplementedError:
            return CapabilityResult(
                ok=False, data={"status": "not_implemented", "op": op},
                error="model 后端未接线，暂无法管理模型",
            )
        except Exception as e:
            return CapabilityResult(ok=False, data={}, error=f"model 操作失败: {e}")
        return CapabilityResult(ok=True, data=result)

    bus.on("model", handle, meta={
        "description": DESCRIPTION,
        "ops": ["list", "select", "switch", "create"],
    })
