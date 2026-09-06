"""内置能力插件：Skills 入口（唯一总线节点，渐进披露文件级技能）。

按 Claude/Anthropic Skills 约定，技能 = 目录 + SKILL.md + 可选附件，是**文件/指令
资源**。但 manifest 只暴露一个能力入口 ``skill``，**具体技能的概要不常驻清单**——
Agent 需要时才逐层索取（渐进披露，省 token）：

- L1  ``skill`` find  → 技能目录（每个技能的 name+一句 description），供选择
- L2  ``skill`` read  → 某技能 SKILL.md 指令全文 + 附件 filemap
- L3  ``skill`` resource → 按需读某技能下一个附件（references/templates/scripts/assets）
- L4  ``skill`` exec  → 进程内执行某技能 scripts/ 下的脚本（约定暴露 ``run(args)->dict``）

选具体技能用 payload 的 ``skill`` 字段（如 ``devops/deploy-k8s``），不必进 manifest。

设计要点：
- manifest 只出现 ``skill`` 一条；具体技能是目录/文件资源，不注册总线条目。
- 执行信号收敛于 ``skill`` 的 exec op，aspect 护栏（sandbox/permission）只签此处。
- 进程内执行（首期）：脚本在当前进程加载，护栏好接。
- 插件文件只做入口分发：任何具体技能（含原内置 code）一律置于 skills/ 目录，
  skill.py 不硬编码任何技能、不 dispatch 任何节点，与 tools 等能力相互独立。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from libcore.kernel.bus import Dispatch, CapabilityResult

# 可选的技能子目录（附件），用于构造 filemap
_SUBDIRS = ("references", "templates", "scripts", "assets")

DESCRIPTION = (
    "技能库入口（manifest 唯一技能能力）：点名我即可。find 列出技能目录（name+描述概要），"
    "再用 payload['skill'] 指定某技能：read 取 SKILL.md 指令全文、resource 读附件、exec 执行 scripts/ 脚本。"
    "技能概要不常驻清单，需要时点我索取。具体技能一律放 skills/ 目录（SKILL.md + 可选附件），"
    "插件文件只做入口分发，不硬编码任何技能。"
)


def _default_skills_dir() -> Path:
    """默认技能库目录：本文件上溯到 libcore/plugins/ 的 resources/skills/。"""
    here = Path(__file__).resolve().parent  # .../libcore/plugins/capabilities
    return here.parent / "resources" / "skills"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 Claude 风格 SKILL.md：取 ``---`` 包裹的 YAML frontmatter 与正文。"""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), parts[2].strip()
    return {}, text.strip()


def _safe_resolve(root: Path, rel: str) -> Optional[Path]:
    """把相对路径解析到技能目录内，防越权（.. / 绝对路径）。"""
    try:
        p = (root / rel).resolve()
    except OSError:
        return None
    return p if p.is_relative_to(root.resolve()) else None


class SkillEngine:
    """技能库 + 执行器。技能库目录可注入（默认 libcore/skills）。

    与总线零耦合：本引擎不 dispatch 任何节点，仅读写技能库文件并产出指引。
    """

    def __init__(self, skills_dir: Optional[Path] = None) -> None:
        self._dir = (skills_dir or _default_skills_dir()).resolve()

    # ---- 索引（find）----

    def find(self, keyword: str = "") -> CapabilityResult:
        """扫描技能库目录，收集索引。"""
        index: list[dict] = []
        if not self._dir.is_dir():
            return CapabilityResult(ok=False, error=f"技能库目录不存在：{self._dir}")
        kw = keyword.lower()
        for skmd in sorted(self._dir.rglob("SKILL.md")):
            meta, _ = _parse_frontmatter(skmd.read_text(encoding="utf-8"))
            category = str(skmd.parent.parent.name)  # 技能目录的上层分类
            name = meta.get("name") or skmd.parent.name
            description = meta.get("description") or ""
            if kw and kw not in name.lower() and kw not in description.lower():
                continue
            index.append({
                "name": name,
                "category": category,
                "description": description,
                "path": str(skmd.parent.relative_to(self._dir)),
            })
        return CapabilityResult(ok=True, data={"skills": index, "count": len(index)})

    # ---- read：技能指令 + filemap ----

    def read(self, rel: str) -> CapabilityResult:
        sp = _safe_resolve(self._dir, rel)
        if sp is None or not sp.is_dir():
            return CapabilityResult(ok=False, error=f"技能不存在：{rel!r}")
        skmd = sp / "SKILL.md"
        if not skmd.is_file():
            return CapabilityResult(ok=False, error=f"{rel} 缺少 SKILL.md")
        meta, body = _parse_frontmatter(skmd.read_text(encoding="utf-8"))
        filemap = {}
        for sub in _SUBDIRS:
            d = sp / sub
            if d.is_dir():
                filemap[sub] = sorted(
                    str(p.relative_to(sp))
                    for p in d.rglob("*")
                    if p.is_file() and "__pycache__" not in str(p)
                )
        return CapabilityResult(ok=True, data={
            "name": meta.get("name") or sp.name,
            "metadata": {k: v for k, v in meta.items() if k != "name"},
            "instruction": body,
            "filemap": filemap,
        })

    # ---- resource：按需读附件 ----

    def resource(self, rel: str, path: str) -> CapabilityResult:
        sp = _safe_resolve(self._dir, rel)
        if sp is None or not sp.is_dir():
            return CapabilityResult(ok=False, error=f"技能不存在：{rel!r}")
        fp = _safe_resolve(sp, path)
        if fp is None or not fp.is_file():
            return CapabilityResult(ok=False, error=f"附件不存在：{rel}/{path}")
        try:
            return CapabilityResult(ok=True, data={
                "path": path,
                "content": fp.read_text(encoding="utf-8"),
            })
        except UnicodeDecodeError:
            return CapabilityResult(ok=False, error=f"{path} 非文本文件（不支持二进制）")

    # ---- exec：进程内跑 scripts/ 脚本 ----

    def exec(self, rel: str, script: str, args: Optional[Dict[str, Any]] = None) -> CapabilityResult:
        sp = _safe_resolve(self._dir, rel)
        if sp is None or not sp.is_dir():
            return CapabilityResult(ok=False, error=f"技能不存在：{rel!r}")
        fp = _safe_resolve(sp, script)
        if fp is None or not fp.is_file() or fp.suffix != ".py":
            return CapabilityResult(ok=False, error=f"脚本不存在：{rel}/{script}（需 .py）")
        try:
            mod_name = f"_libcore_skill_{sp.name}_{fp.stem}"
            spec = importlib.util.spec_from_file_location(
                mod_name, fp, submodule_search_locations=[]
            )
            if spec is None or spec.loader is None:
                return CapabilityResult(ok=False, error=f"无法加载脚本 {script}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            run = getattr(module, "run", None)
            if not callable(run):
                return CapabilityResult(
                    ok=False, error=f"{script} 未暴露 run(args)->dict 约定",
                )
            output = run(args or {})
            if not isinstance(output, dict):
                output = {"result": output}
            return CapabilityResult(ok=True, data=output)
        except Exception as e:  # noqa: BLE001 - 进程内执行失败如实返回
            return CapabilityResult(ok=False, error=f"{script} 执行失败：{e}")


# ---- 总线装配 ----

def register(bus) -> None:
    engine = SkillEngine()

    async def handle(d: Dispatch) -> CapabilityResult:
        op = d.op or (d.payload.get("op") or "")
        rel = str(d.payload.get("skill") or d.payload.get("name") or "")
        if op == "find":
            return engine.find(str(d.payload.get("keyword") or ""))
        if op == "read":
            return engine.read(rel)
        if op == "resource":
            return engine.resource(rel, str(d.payload.get("path") or ""))
        if op == "exec":
            return engine.exec(
                rel,
                str(d.payload.get("script") or ""),
                d.payload.get("args"),
            )
        return CapabilityResult(
            ok=False, error=f"未知操作 {op}（期望 find/read/resource/exec）",
        )

    # 单一契约点 skill（manifest 只暴露这一个技能入口），op 分发（对齐 file/todo 分发模式）
    bus.on("skill", handle, meta={
        "description": DESCRIPTION,
        "ops": ["find", "read", "resource", "exec"],
    })