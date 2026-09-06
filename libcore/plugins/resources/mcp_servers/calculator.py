"""MCP 工具定义：calculator（纯计算，本地立即执行，示例真实实现）。

演示 MCP 门面渐进披露的 run 链路。add/sub/mul/div 均为纯函数，
不依赖网络 / 远端 server，便于内核与护栏先行闭环验证。
"""
from __future__ import annotations

from libcore.kernel.bus import Dispatch, CapabilityResult


def _num(value, name: str, default):
    try:
        return value if value is None else type(default)(value)
    except (TypeError, ValueError):
        return default


def _handle(d: Dispatch) -> CapabilityResult:
    op = d.op or "add"
    a = _num(d.payload.get("a"), "a", 0)
    b = _num(d.payload.get("b"), "b", 0)
    if op == "add":
        value = a + b
    elif op == "sub":
        value = a - b
    elif op == "mul":
        value = a * b
    elif op == "div":
        if b == 0:
            return CapabilityResult(ok=False, error="除数不能为 0",
                                    data={"op": op, "a": a, "b": b})
        value = a / b
    else:
        return CapabilityResult(ok=False, error=f"calculator 不支持操作 {op!r}（支持 add/sub/mul/div）")
    return CapabilityResult(ok=True, data={"op": op, "a": a, "b": b, "result": value})


TOOLS = {
    "calculator": {
        "name": "calculator",
        "description": "MCP 计算器：add/sub/mul/div 四则运算（本地纯函数示例）",
        "ops": ["add", "sub", "mul", "div"],
        "schema": {
            "add": {"a": "int|float, 第一个操作数", "b": "int|float, 第二个操作数"},
            "sub": {"a": "int|float", "b": "int|float"},
            "mul": {"a": "int|float", "b": "int|float"},
            "div": {"a": "int|float", "b": "int|float, 除数（不能为 0）"},
        },
        "handler": _handle,
    }
}