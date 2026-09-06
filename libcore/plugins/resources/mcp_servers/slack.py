"""MCP 工具定义：slack（占位，后端未接线）。

用于演示 MCP 门面对"未实现后端"的诚实披露：find/read 能正常列工具与 schema，
但 run 如实返回 not_implemented（绝不编造发送成功），与 web.search 一致。
"""
from __future__ import annotations

from libcore.kernel.bus import Dispatch, CapabilityResult


def _handle(d: Dispatch) -> CapabilityResult:
    op = d.op or "send"
    return CapabilityResult(
        ok=False,
        error="slack 后端未接线，暂无法发送消息",
        data={"status": "not_implemented", "op": op},
    )


TOOLS = {
    "slack": {
        "name": "slack",
        "description": "MCP 消息服务：send 向指定频道发消息（当前为占位，后端未接线）",
        "ops": ["send"],
        "schema": {"send": {"channel": "str, 目标频道", "text": "str, 消息正文"}},
        "handler": _handle,
    }
}