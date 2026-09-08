"""MCP 能力节点测试：find / read / run 与 _internal 注入健壮性。

bus.dispatch() 是异步的，用 asyncio.run() 包裹（不依赖 pytest-asyncio）。
"""
from __future__ import annotations

import asyncio

from libcore.kernel.bus import Dispatch, CapabilityResult
from libcore.plugins.capabilities.mcp import McpEngine, McpSpec


def _dispatch(bus, target="mcp", op="run", payload=None):
    return Dispatch(target=target, op=op, payload=payload or {})


def _call(bus, d):
    return asyncio.run(bus.dispatch(d))


def _handler_none_data(d: Dispatch) -> CapabilityResult:
    """handler 返回 ok=True 但 data=None（真实组件可能如此）。"""
    return CapabilityResult(ok=True, data=None)


class TestMcpRunInternalInjection:
    def test_run_with_none_data_does_not_crash(self):
        """data=None 时 _internal 注入不得抛 TypeError，且保持 data=None。"""
        engine = McpEngine()
        engine._tools["calc"] = McpSpec(
            key="calc", name="calc", description="d",
            ops=["add"], handler=_handler_none_data,
        )
        result = engine.run("calc", "add", {"a": 1, "b": 2})
        assert result.ok
        assert result.data is None

    def test_run_with_dict_data_injects_internal(self):
        """data 为 dict 时正常注入 _internal 子调用细节。"""
        engine = McpEngine()
        engine._tools["calc"] = McpSpec(
            key="calc", name="calc", description="d",
            ops=["add"], handler=lambda d: CapabilityResult(ok=True, data={"sum": 3}),
        )
        result = engine.run("calc", "add", {"a": 1, "b": 2})
        assert result.ok
        assert result.data["sum"] == 3
        assert result.data["_internal"] == {
            "sub_target": "calc", "sub_op": "add", "sub_args": {"a": 1, "b": 2},
        }
