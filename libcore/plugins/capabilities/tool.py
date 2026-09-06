"""内核能力插件：Tools 门面（唯一总线节点，渐进披露代码工具）。

注意：文件名取 ``tool.py`` 而非 ``tools.py``，是为了避免与同目录 ``tools/``
（工具定义子包）同名——否则 ``import libcore.plugins.capabilities.tools``
会错误命中工具定义包而非本门面。总线暴露的 target 仍是 ``tools``。

与 ``skill`` 门面对称：manifest 只暴露 ``tools`` 一个工具入口，**具体工具的
schema 不常驻清单**——Agent 需要时才逐层索取（渐进披露，省 token），
对齐 Anthropic Tool Search：

- L1  ``tools`` find  → 工具索引（key + 名称 + 一句 description），供选择
- L2  ``tools`` read  → 某工具的完整参数 schema（按需展开）
- L3  ``tools`` run   → 执行某工具（payload 传 name + tool_op + args）

具体工具是**代码定义**（``libcore/tools/`` 的 ``TOOLS`` 纯文件注册表，与
``libcore/skills/`` 平级的**非总线节点**定义库，不 import、不注册，门面启动时
直接文件扫描聚合），不是总线节点、不进 manifest。执行信号收敛于 ``tools`` 的
run op，aspect 护栏（sandbox / permission）只签此处。
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from libcore.kernel.bus import Dispatch, CapabilityResult

DESCRIPTION = (
    "工具库入口（manifest 唯一工具能力）：点名我即可。find 列出工具索引（key+名称+一句描述，不含 schema），"
    "再用 payload['name'] 指定某工具：read 取完整参数 schema、run 执行（tool_op 指定操作、args 传参数）。"
    "工具 schema 不常驻清单，需要时点我索取。"
)


@dataclass
class ToolSpec:
    """单一工具定义：name/description/ops/schema + 执行 handler。"""

    name: str
    description: str
    ops: list
    handler: Callable[[Dispatch], CapabilityResult]
    schema: dict = field(default_factory=dict)
    key: str = ""


def _default_tools_root() -> Path:
    """默认工具定义库：libcore/plugins/resources/tools/（非总线节点定义目录）。"""
    # 本文件位于 libcore/plugins/capabilities/，父目录 plugins 下进 resources/tools
    return Path(__file__).resolve().parent.parent / "resources" / "tools"


class ToolEngine:
    """工具库 + 执行器。工具定义目录可注入（默认 libcore/tools）。"""

    def __init__(self, tools_root: Optional[Path] = None) -> None:
        self._root = (tools_root or _default_tools_root()).resolve()
        self._tools: Dict[str, ToolSpec] = self._load()

    # ---- 加载：扫描 tools/ 子包，聚合每模块暴露的 TOOLS ----

    def _load(self) -> Dict[str, ToolSpec]:
        tools: Dict[str, ToolSpec] = {}
        if not self._root.is_dir():
            return tools
        for path in sorted(self._root.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module = self._load_module(f"_libcore_tool_{path.stem}", path)
            registry = getattr(module, "TOOLS", None)
            if not isinstance(registry, dict):
                continue
            for key, entry in registry.items():
                name = entry.get("name") or key
                tools[key] = ToolSpec(
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

    # ---- read：按需展开某工具完整 schema ----

    def read(self, key: str) -> CapabilityResult:
        s = self._tools.get(key)
        if s is None:
            return CapabilityResult(ok=False, error=f"工具不存在：{key!r}")
        return CapabilityResult(ok=True, data={
            "key": key,
            "name": s.name,
            "ops": s.ops,
            "description": s.description,
            "schema": s.schema,
        })

    # ---- run：执行某工具 ----

    def run(self, key: str, op: str, args: Dict[str, Any]) -> CapabilityResult:
        s = self._tools.get(key)
        if s is None:
            return CapabilityResult(ok=False, error=f"工具不存在：{key!r}")
        if op and s.ops and op not in s.ops:
            return CapabilityResult(
                ok=False,
                error=f"工具 {key} 不支持操作 {op!r}（支持 {s.ops}）",
            )
        # 工具 handler 统一读 Dispatch：target=工具名，op=具体操作，payload=args
        sub = Dispatch(target=key, op=op or "run", payload=dict(args or {}))
        return s.handler(sub)


# ---- 总线装配 ----

def register(bus) -> None:
    engine = ToolEngine()

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

    # 单一契约点 tools（manifest 只暴露这一个工具入口），op 分发（对齐 skill 范式）
    bus.on("tools", handle, meta={
        "description": DESCRIPTION,
        "ops": ["find", "read", "run"],
    })