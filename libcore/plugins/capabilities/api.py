"""内置能力插件：api —— agent 调度它获取数据（agentOS 系统能力）。

语义：agent 接管系统核心后，**数据获取**是 agent 可调度的能力节点。
agent 决定调哪个 API、取哪些数据、怎么组织，产出数据契约。

产出约定：``data["data"]`` 为获取到的数据；``data["schema"]`` 为数据契约（可选）。
无后端/异常 → 降级为 not_implemented（诚实披露，绝不编造数据）。

装配（零耦合，可拆卸）：
- 默认从 payload 读取 ``payload["endpoint"]`` / ``payload["params"]``，经注入的 ``fetcher`` 获取；
- ``register(bus, fetcher=)`` 可注入真实 HTTP/服务调用函数（签名 ``fetcher(endpoint, params) -> dict``）；
- 本节点不 dispatch 任何其他节点。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from libcore.kernel.bus import Dispatch, CapabilityResult

DESCRIPTION = (
    "系统能力节点：agent 调度我获取数据。决定调哪个 API、取哪些数据、怎么组织，"
    "产出数据契约（data['data'] + data['schema']）。无后端降级为 not_implemented。"
)

# fetcher 类型：接收 endpoint + params，返回数据 dict
Fetcher = Callable[[str, Dict[str, Any]], Dict[str, Any]]


def _default_fetcher(endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """默认 fetcher：占位，诚实披露未接线（绝不编造数据）。"""
    raise NotImplementedError(f"api 后端未接线，无法获取: {endpoint}")


def register(bus, fetcher: Optional[Fetcher] = None) -> None:
    """注册 api 能力节点。``fetcher`` 可注入真实数据获取函数；缺省占位降级。"""
    fetch = fetcher or _default_fetcher

    def handle(d: Dispatch) -> CapabilityResult:
        op = d.op or "get"
        endpoint = str(d.payload.get("endpoint") or "")
        params = d.payload.get("params") or {}
        if not endpoint:
            return CapabilityResult(ok=False, data={}, error="api 需要 endpoint")
        try:
            data = fetch(endpoint, params)
        except NotImplementedError:
            return CapabilityResult(
                ok=False, data={"status": "not_implemented", "op": op, "endpoint": endpoint},
                error=f"api 后端未接线，暂无法获取: {endpoint}",
            )
        except Exception as e:
            return CapabilityResult(ok=False, data={}, error=f"api 获取失败: {e}")
        return CapabilityResult(ok=True, data={"data": data, "schema": params.get("schema")})

    bus.on("api", handle, meta={
        "description": DESCRIPTION,
        "ops": ["get"],
    })
