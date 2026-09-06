"""内置能力插件：prompt —— 决策输入节点（系统指令，可组合角色/风格/领域知识）。

语义领域：**system 指令**（角色 / 风格 / 领域知识）。与 context/memory 平级 peer，互不认识。
产出约定：``data["messages"]`` 为注入决策者的系统指令；缺省仍返回内置默认指令（单核可运行），
不降级为 None。

装配（零耦合，可拆卸）：可用 ``build_prompt`` 组合若干片段（角色/风格/领域知识），
替换默认指令。每个片段独立开关，缺失即跳过，不阻塞。
"""
from __future__ import annotations

from typing import Callable, Optional

from libcore.kernel.bus import Dispatch, CapabilityResult
from libcore.llm.spi import LLMMsg

DESCRIPTION = (
    "决策输入节点：内核每轮决策前点名我，产出强化系统指令消息（data['messages']）注入决策者。"
    "可组合 角色/风格/领域知识 片段。"
)

# 提示词片段：接收 Dispatch，返回一段指令文本（可为空）
Segment = Callable[[Dispatch], Optional[str]]


# ── 旧 Prompt 组件族迁移：按 priority 排序拼接（对齐旧 ChainRunner）──────

# 旧组件 priority 映射（对齐 prompt_components/builtins/*.py）：
#   safety(10) < system_basic/agent/rag(20) < reasoning_style/code_skill(30) < format_json(99)
PROMPT_PRIORITY = {
    "safety": 10,
    "system": 20,
    "style": 30,
    "domain": 30,
    "format": 99,
}


def build_prompt_chain(
    *,
    safety: Optional[Segment] = None,
    system: Optional[Segment] = None,
    style: Optional[Segment] = None,
    domain: Optional[Segment] = None,
    format: Optional[Segment] = None,
) -> Segment:
    """装配 prompt 链：按 priority 排序拼接若干片段（对齐旧 ChainRunner 语义）。

    旧 ``IPromptComponent.render() -> (segment, role, priority)`` 按 priority 升序拼接，
    这里把各片段映射到固定 priority（safety 最前、format 最后），缺省段跳过。

    Args:
        safety:  安全对齐（旧 libcore.safety.default_alignment，priority 10）
        system:  基础系统指令（旧 libcore.system.basic/agent/rag，priority 20）
        style:   推理风格（旧 libcore.system.agent_reasoning_style，priority 30）
        domain:  领域知识（priority 30）
        format:  输出格式约束（旧 libcore.format.structured_json，priority 99）

    Returns:
        一个组合片段（按 priority 排序拼接，缺省段跳过）。
    """
    items = [
        (PROMPT_PRIORITY["safety"], safety),
        (PROMPT_PRIORITY["system"], system),
        (PROMPT_PRIORITY["style"], style),
        (PROMPT_PRIORITY["domain"], domain),
        (PROMPT_PRIORITY["format"], format),
    ]
    items = [(p, s) for p, s in items if s is not None]
    items.sort(key=lambda t: t[0])

    def compose(d: Dispatch) -> str:
        lines: list[str] = []
        for _p, s in items:
            text = s(d)
            if isinstance(text, str) and text.strip():
                lines.append(text.strip())
        return "\n".join(lines)

    return compose


def build_prompt(
    *,
    role: Optional[Segment] = None,
    style: Optional[Segment] = None,
    domain: Optional[Segment] = None,
) -> Segment:
    """装配 prompt 节点：按 role → style → domain 顺序组合系统指令，缺省段跳过。

    Args:
        role/domain/style: 各为返回一段文本的片段函数；返回 None/"" 表示该段无内容，自动跳过。

    Returns:
        一个组合片段（可作为 prompt 节点 handler 的数据源）。
    """
    segments = [s for s in (role, style, domain) if s is not None]

    def compose(d: Dispatch) -> str:
        lines: list[str] = []
        for s in segments:
            text = s(d)
            if isinstance(text, str) and text.strip():
                lines.append(text.strip())
        return "\n".join(lines)

    return compose


def _default_segment(d: Dispatch) -> str:
    """默认 prompt：内置调度员系统指令（单核最小可运行）。

    文本唯一真相源 = 配置 llm.yaml `system_prompt`（缺省回退 llm 包内建兜底）。
    """
    from libcore.llm import defaults as _llm_defaults

    return _llm_defaults().get("system_prompt", "")


def register(bus, segment: Optional[Segment] = None) -> None:
    """注册 prompt 输入节点。``segment`` 可传组合好的片段；缺省用内置默认指令。"""
    compose = segment or _default_segment

    def handle(d: Dispatch) -> CapabilityResult:
        text = compose(d) or ""
        return CapabilityResult(ok=True, data={"messages": [LLMMsg("system", text)]})

    bus.on("prompt", handle, meta={
        "description": DESCRIPTION,
        "ops": ["run"],
    })
