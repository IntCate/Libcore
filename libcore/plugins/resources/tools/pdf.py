"""工具定义：解析 PDF（收进 tools 门面，占位实现）。

原为独立占位插件 tool.pdf，收敛为 tools 门面下的 pdf 子工具。当前为占位，
返回示例 pages/chunks，真实 PDF 解析待落地（见 backlog）。
handler 统一签名 ``(d: Dispatch) -> CapabilityResult``。
"""
from __future__ import annotations

from libcore.kernel.bus import Dispatch, CapabilityResult


def _handle(d: Dispatch) -> CapabilityResult:
    return CapabilityResult(ok=True, data={
        "pages": 300,
        "chunks": 42,
        "source": "placeholder",
    })


TOOLS = {
    "pdf": {
        "name": "pdf",
        "description": "解析 PDF 提取文本与分块（当前为占位实现）",
        "ops": ["parse"],
        "schema": {"parse": {}},
        "handler": _handle,
    }
}