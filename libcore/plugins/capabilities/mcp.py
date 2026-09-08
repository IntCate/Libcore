"""内核能力插件：MCP 门面（唯一总线节点，渐进披露 MCP 工具）。

与 ``tools`` / ``skill`` 同为一级"真入口"。manifest 只暴露 ``mcp`` 一个 MCP 能力，
**具体 MCP 工具的 schema 不常驻清单**——Agent 需要时才逐层索取（渐进披露，省 token）：

- L1  ``mcp`` find  → 工具索引（key + 名称 + 一句 description），供选择
- L2  ``mcp`` read  → 某 MCP 工具的完整参数 schema（按需展开）
- L3  ``mcp`` run   → 执行某 MCP 工具（payload 传 name + tool_op + args）

具体 MCP 工具是**代码定义**（``libcore/mcp_servers/`` 的 ``TOOLS`` 纯文件注册表，与
``libcore/tools/`` 平级的**非总线节点**定义库，不 import、不注册，门面启动时直接
文件扫描聚合），不是总线节点、不进 manifest。执行信号收敛于 ``mcp`` 的 run op，
aspect 护栏（sandbox / permission / circuit_breaker / audit）与其他门面一样只签此处。

设计说明：真实部署时 ``mcp.run`` 应向**远端 MCP server** 透发（走 MCP 协议，
client 实现归组件层 services，不 import 进内核顶部）。首期 ``libcore/mcp_servers/``
用本地代码定义承载，便于内核/护栏先行闭环验证；后端未接线的服务如实
返回 ``not_implemented``，绝不为凑数据编造。
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from libcore.kernel.bus import Dispatch, CapabilityResult

DESCRIPTION = (
    "MCP 工具库入口（manifest 唯一 MCP 能力）：点名我即可。find 列出 MCP 工具/服务索引"
    "（key+名称+一句描述，不含 schema），再用 payload['name'] 指定某工具：read 取完整参数"
    " schema、run 执行（tool_op 指定操作、args 传参数）。schema 不常驻清单，需要时点我索取。"
)


@dataclass
class McpSpec:
    """单一 MCP 工具定义：name/description/ops/schema + 执行 handler。"""

    name: str
    description: str
    ops: list
    handler: Callable[[Dispatch], CapabilityResult]
    schema: dict = field(default_factory=dict)
    key: str = ""


def _default_mcp_root() -> Path:
    """默认 MCP 工具定义库：libcore/plugins/resources/mcp_servers/（非总线节点定义目录）。"""
    # 本文件位于 libcore/plugins/capabilities/，父目录 plugins 下进 resources/mcp_servers
    return Path(__file__).resolve().parent.parent / "resources" / "mcp_servers"


class McpEngine:
    """MCP 工具库 + 执行器。定义目录可注入（默认 libcore/mcp_servers）。"""

    def __init__(self, mcp_root: Optional[Path] = None) -> None:
        self._root = (mcp_root or _default_mcp_root()).resolve()
        self._tools: Dict[str, McpSpec] = self._load()

    # ---- 加载：扫描 mcp/ 子包，聚合每模块暴露的 TOOLS ----

    def _load(self) -> Dict[str, McpSpec]:
        tools: Dict[str, McpSpec] = {}
        if not self._root.is_dir():
            return tools
        for path in sorted(self._root.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module = self._load_module(f"_libcore_mcp_{path.stem}", path)
            registry = getattr(module, "TOOLS", None)
            if not isinstance(registry, dict):
                continue
            for key, entry in registry.items():
                name = entry.get("name") or key
                tools[key] = McpSpec(
                    key=key,
                    name=name,
                    description=entry.get("description") or "",
                    ops=list(entry.get("ops") or []),
                    schema=entry.get("schema") or {},
                    handler=entry["handler"],
                )
        return tools

    @staticmethod
    def _load_module(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    # ---- find：索引（不含完整 schema，省 token）----

    def find(self, keyword: str = "") -> CapabilityResult:
        kw = keyword.lower()
        out = []
        for key, s in self._tools.items():
            hay = f"{key} {s.name} {s.description}".lower()
            if kw and kw not in hay:
                continue
            out.append({
                "key": key,
                "name": s.name,
                "ops": s.ops,
                "description": s.description,
            })
        return CapabilityResult(ok=True, data={"tools": out, "count": len(out)})

    # ---- read：按需展开某 MCP 工具完整 schema ----

    def read(self, key: str) -> CapabilityResult:
        s = self._tools.get(key)
        if s is None:
            return CapabilityResult(ok=False, error=f"MCP 工具不存在：{key!r}")
        return CapabilityResult(ok=True, data={
            "key": key,
            "name": s.name,
            "ops": s.ops,
            "description": s.description,
            "schema": s.schema,
        })

    # ---- run：执行某 MCP 工具 ----

    def run(self, key: str, op: str, args: Dict[str, Any]) -> CapabilityResult:
        s = self._tools.get(key)
        if s is None:
            return CapabilityResult(ok=False, error=f"MCP 工具不存在：{key!r}")
        if op and s.ops and op not in s.ops:
            return CapabilityResult(
                ok=False,
                error=f"MCP 工具 {key} 不支持操作 {op!r}（支持 {s.ops}）",
            )
        sub = Dispatch(target=key, op=op or "run", payload=dict(args or {}))
        result = s.handler(sub)
        # 暴露内部子调用细节给 tracing/logging（不发布到总线，仅随结果上抛）：
        # 具体 MCP 工具 handler 是开放组件，不走总线，但门面把"内部调了谁、什么参数"
        # 塞进 result.data，使 tracing 能还原到具体工具这一层，而不只是 mcp run 门面。
        if isinstance(result, CapabilityResult):
            # 仅当 data 为 dict 时才注入 _internal（data=None 时保持原样，不崩溃）
            if isinstance(result.data, dict):
                result.data["_internal"] = {
                    "sub_target": key,
                    "sub_op": op or "run",
                    "sub_args": dict(args or {}),
                }
        return result


# ---- 总线装配 ----

def register(bus) -> None:
    engine = McpEngine()

    def handle(d: Dispatch) -> CapabilityResult:
        op = d.op or (d.payload.get("op") or "")
        name = str(d.payload.get("name") or "")
        args = d.payload.get("args")
        if not isinstance(args, dict):
            args = {} if args is None else {"value": args}
        if op == "find":
            return engine.find(str(d.payload.get("keyword") or ""))
        if op == "read":
            return engine.read(name)
        if op == "run":
            return engine.run(
                name,
                str(d.payload.get("tool_op") or ""),
                dict(args),
            )
        return CapabilityResult(
            ok=False, error=f"未知操作 {op}（期望 find/read/run）",
        )

    # 单一契约点 mcp（manifest 只暴露这一个 MCP 入口），op 分发（对齐 tools/skill 范式）
    bus.on("mcp", handle, meta={
        "description": DESCRIPTION,
        "ops": ["find", "read", "run"],
    })