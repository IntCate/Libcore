"""工具定义：本地文件 读 / 写 / 搜索（收进 tools 门面，不再独立注册总线节点）。

由旧架构 FileReadTool / FileWriteTool / FileSearchTool 重写而来。
handler 统一签名 ``(d: Dispatch) -> CapabilityResult``，按 ``d.op`` 分发 read/write/search。
"""
from __future__ import annotations

from pathlib import Path

from libcore.kernel.bus import Dispatch, CapabilityResult


def _read(d: Dispatch) -> CapabilityResult:
    path = str(d.payload.get("path") or "")
    if not path:
        return CapabilityResult(ok=False, error="缺少必填参数 path")
    try:
        p = Path(path)
        content = p.read_text(encoding="utf-8")
        return CapabilityResult(ok=True, data={
            "path": str(p.resolve()),
            "bytes": len(content.encode("utf-8")),
            "content": content,
        })
    except Exception as e:  # noqa: BLE001
        return CapabilityResult(ok=False, error=str(e))


def _write(d: Dispatch) -> CapabilityResult:
    path = str(d.payload.get("path") or "")
    content = d.payload.get("content")
    if not path or content is None:
        return CapabilityResult(ok=False, error="缺少必填参数 path/content")
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return CapabilityResult(ok=True, data={
            "path": str(p.resolve()),
            "bytes": len(str(content).encode("utf-8")),
            "written": True,
        })
    except Exception as e:  # noqa: BLE001
        return CapabilityResult(ok=False, error=str(e))


def _search(d: Dispatch) -> CapabilityResult:
    pattern = d.payload.get("pattern")
    base = str(d.payload.get("path") or ".")
    if not pattern:
        return CapabilityResult(ok=False, error="缺少必填参数 pattern")
    try:
        matches = sorted(str(x) for x in Path(base).glob(pattern))
        return CapabilityResult(ok=True, data={"matches": matches, "count": len(matches)})
    except Exception as e:  # noqa: BLE001
        return CapabilityResult(ok=False, error=str(e))


def _handle(d: Dispatch) -> CapabilityResult:
    op = d.op or d.payload.get("op") or "read"
    fn = {"read": _read, "write": _write, "search": _search}.get(op)
    if fn is None:
        return CapabilityResult(ok=False, error=f"未知操作 {op}")
    return fn(d)


TOOLS = {
    "file": {
        "name": "file",
        "description": "读写本地文本文件、按 glob 匹配搜索文件名",
        "ops": ["read", "write", "search"],
        "schema": {
            "read": {"path": "str, 要读取的文件路径"},
            "write": {"path": "str, 目标文件路径", "content": "str, 要写入的文本内容"},
            "search": {"pattern": "str, glob 匹配模式", "path": "str, 搜索基准目录（默认 .）"},
        },
        "handler": _handle,
    }
}