"""工具定义：待办（收进 tools 门面，不再独立注册总线节点）。

由旧架构 TodoAdd/List/Done 重写而来。不引入旧 session 体系：改用 payload 里显式
``session`` 键（缺省 "default"）分区。内存存储，进程重启即丢失。
handler 统一签名 ``(d: Dispatch) -> CapabilityResult``，按 ``d.op`` 分发 add/list/done。
"""
from __future__ import annotations

from libcore.kernel.bus import Dispatch, CapabilityResult

_VALID_PRIORITIES = ("low", "medium", "high")
_VALID_STATUSES = ("all", "pending", "done")

# session → 待办列表；session → 自增计数（单线程事件循环，无需加锁）
_todos: dict[str, list] = {}
_counters: dict[str, int] = {}


def _session(payload) -> str:
    return str(payload.get("session") or "default")


def _add(d: Dispatch) -> CapabilityResult:
    content = d.payload.get("content")
    if not content:
        return CapabilityResult(ok=False, error="缺少必填参数 content")
    priority = d.payload.get("priority", "medium")
    if priority not in _VALID_PRIORITIES:
        priority = "medium"
    sid = _session(d.payload)
    bucket = _todos.setdefault(sid, [])
    _counters[sid] = _counters.get(sid, 0) + 1
    todo = {
        "id": str(_counters[sid]),
        "content": content,
        "priority": priority,
        "status": "pending",
    }
    bucket.append(todo)
    return CapabilityResult(ok=True, data={"todo": todo})


def _list(d: Dispatch) -> CapabilityResult:
    status = d.payload.get("status", "all")
    if status not in _VALID_STATUSES:
        status = "all"
    todos = list(_todos.get(_session(d.payload), []))
    if status != "all":
        todos = [t for t in todos if t.get("status") == status]
    return CapabilityResult(ok=True, data={"todos": todos, "count": len(todos)})


def _done(d: Dispatch) -> CapabilityResult:
    todo_id = str(d.payload.get("id") or "")
    if not todo_id:
        return CapabilityResult(ok=False, error="缺少必填参数 id")
    for t in _todos.get(_session(d.payload), []):
        if t.get("id") == todo_id:
            t["status"] = "done"
            return CapabilityResult(ok=True, data={"todo": t})
    return CapabilityResult(ok=False, error=f"未找到待办 id={todo_id}")


def _handle(d: Dispatch) -> CapabilityResult:
    op = d.op or d.payload.get("op") or "list"
    fn = {"add": _add, "list": _list, "done": _done}.get(op)
    if fn is None:
        return CapabilityResult(ok=False, error=f"未知操作 {op}")
    return fn(d)


TOOLS = {
    "todo": {
        "name": "todo",
        "description": "往会话待办增查改记录（add/list/done），可按状态过滤",
        "ops": ["add", "list", "done"],
        "schema": {
            "add": {"content": "str, 待办内容", "priority": "low|medium|high（默认 medium）", "session": "str, 分区（默认 default）"},
            "list": {"status": "all|pending|done（默认 all）", "session": "str"},
            "done": {"id": "str, 待办 id", "session": "str"},
        },
        "handler": _handle,
    }
}