"""资源工具测试：pdf 未实现语义（不得伪造数据）。

对齐 web.search 的 not_implemented 约定：未接线的后端如实返回，绝不编造。
"""
from __future__ import annotations

from libcore.plugins.resources.tools import pdf as pdf_tool
from libcore.kernel.bus import Dispatch


def test_pdf_returns_not_implemented():
    """pdf 未实现时必须返回 not_implemented，不得伪造 pages/chunks。"""
    result = pdf_tool._handle(Dispatch(target="pdf", op="parse", payload={}))
    assert not result.ok
    assert result.data["status"] == "not_implemented"
    assert "pages" not in result.data, "不得伪造 pages 数据"
    assert "chunks" not in result.data, "不得伪造 chunks 数据"
