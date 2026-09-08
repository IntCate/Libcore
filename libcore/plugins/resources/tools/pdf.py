"""工具定义：解析 PDF（收进 tools 门面，占位实现）。

原为独立占位插件 tool.pdf，收敛为 tools 门面下的 pdf 子工具。当前为占位，
真实 PDF 解析待落地（见 backlog）。未实现时如实返回 not_implemented，
**绝不伪造 pages/chunks 数据**（对齐 web.search 的约定）。
handler 统一签名 ``(d: Dispatch) -> CapabilityResult``。
"""
from __future__ import annotations

from libcore.kernel.bus import Dispatch, CapabilityResult


def _handle(d: Dispatch) -> CapabilityResult:
    return CapabilityResult(
        ok=False,
        error="PDF 解析后端未配置，暂时无法返回结果",
        data={"status": "not_implemented", "source": "placeholder"},
    )


TOOLS = {
    "pdf": {
        "name": "pdf",
        "description": "解析 PDF 提取文本与分块（当前为占位实现）",
        "ops": ["parse"],
        "schema": {"parse": {}},
        "handler": _handle,
    }
}