"""Agent 角色配置加载器与工厂：按 agents.yaml 的角色组装 (backend + model + prompt + 能力域)。

分层（对齐 llm.yaml 职责分离）：
- 模型【连接】参数（url/key/base_url）只归 libcore/config/llm.yaml 的 `backends:`，
  本模块通过 `backend` 字段【引用】llm.yaml 的连接键，不重复写连接参数；
- agents.yaml 只声明"每个角色"的（backend + model + system_prompt + temperature +
  能力域 capabilities + input_nodes），即"谁用哪个模型 + 大脑指令 + 可点名范围"。

核心产物：
- ``load_profiles()``：把 agents.yaml 解析、校验成 ``{role: AgentProfile}``；
- ``build_reason(role)``：按角色组一个 ``LlmReasonProvider``（未施加能力域）；
- ``scoped(profile, reason)``：包一层 ``ScopedReason``，按角色 capability 白名单
  过滤每轮可见能力清单，给该 agent 划清"可点名范围"；
- ``build_kernel(role, ...)``：按 kind 组装成对应内核实体（kernel/worker/resident）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .kernel.bus.scope import Scope
from .kernel.agent.spi import Action, ReasonProvider
from .llm import (
    has as _llm_has,
    create as _llm_create,
    defaults as _llm_defaults,
)

_SCHEMA_VERSION = 1


# ---------------------------------------------------------------
# 领域模型
# ---------------------------------------------------------------

@dataclass(frozen=True)
class AgentProfile:
    """一个 Agent 角色的已解析配置（模型连接经 llm.yaml 引用解析）。"""

    name: str
    kind: str
    system_prompt: str
    # 模型：backend 引用 llm.yaml backends 键；model 为具体模型名
    backend: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    # 能力白名单：None=不限；否则只暴露匹配的 target（支持 fnmatch 通配）
    capabilities: Optional[List[str]] = None
    input_nodes: Optional[List[Dict[str, Any]]] = field(default=None)


# ---------------------------------------------------------------
# 加载
# ---------------------------------------------------------------

def _normalize_capabilities(caps: Any) -> Optional[List[str]]:
    """把 capabilities 归一化为 list[str]（能力白名单）。

    兼容两种声明形式：
    - list：``["tools", "tool.*"]``（直接白名单）；
    - dict：``{"allow": ["tools", "tool.*"]}``（显式 allow 键）。
    None 原样返回（= 不限能力域）。
    """
    if caps is None:
        return None
    if isinstance(caps, list):
        return list(caps)
    if isinstance(caps, dict):
        allow = caps.get("allow")
        if isinstance(allow, list):
            return list(allow)
        return None
    return None


def _config_root() -> Path:
    """libcore/config/：以本文件（libcore/agents.py）定位。"""
    import libcore
    return Path(libcore.__file__).parent / "config"


def load_raw(path: Optional[str] = None) -> Dict[str, Any]:
    """读取并解析 agents.yaml；缺失或非法返回空 dict（零配置可用）。"""
    p = Path(path) if path else _config_root() / "agents.yaml"
    if not p.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def load_profiles(path: Optional[str] = None) -> Dict[str, AgentProfile]:
    """把 agents.yaml 解析成 ``{role: AgentProfile}``，缺省字段合并 defaults + llm.yaml。

    - schema_version 不符 -> 抛 ValueError（提醒升级）；
    - 每个角色校验 kind / model 必填；backend 引用 llm.yaml backends 键；
    - 系统指令缺省回退 llm.yaml system_prompt（或 llm 包内建兜底）。
    """
    raw = load_raw(path)
    if not raw:
        return {}
    version = raw.get("schema_version", 1)
    if version != _SCHEMA_VERSION:
        raise ValueError(
            f"agents.yaml schema_version={version} 不受支持（当前 {_SCHEMA_VERSION}），请升级配置。"
        )
    d = raw.get("defaults") or {}
    llm = _llm_defaults()

    out: Dict[str, AgentProfile] = {}
    for name, spec in (raw.get("agents") or {}).items():
        if not isinstance(spec, dict):
            continue
        backend = spec.get("backend") or d.get("backend") or llm.get("default_backend")
        model = spec.get("model") or llm.get("default_model")
        if not model:
            raise ValueError(f"角色 {name!r} 缺少 model：既未在 agents.yaml 指定，llm.yaml 也未设 default_model。")
        if backend and not _llm_has(backend):
            continue  # 连接键未知：跳过该角色（不阻塞整表），由装配方感知缺失
        temp = spec.get("temperature", d.get("temperature", llm.get("temperature", 0.2)))
        prompt = (spec.get("system_prompt") or llm.get("system_prompt") or "").strip()
        caps = spec.get("capabilities", d.get("capabilities"))
        out[name] = AgentProfile(
            name=name,
            kind=str(spec.get("kind") or "worker"),
            system_prompt=prompt,
            backend=backend,
            model=model,
            temperature=float(temp) if temp is not None else None,
            capabilities=_normalize_capabilities(caps),
            input_nodes=spec.get("input_nodes", d.get("input_nodes")),
        )
    return out


# ---------------------------------------------------------------
# 组装：按角色生成 backend + reason
# ---------------------------------------------------------------

def resolve_backend(profile: AgentProfile):
    """按 profile 创建 backend 实例（连接参数取自 llm.yaml backends 默认）。"""
    if not profile.backend or not _llm_has(profile.backend):
        raise KeyError(
            f"角色 {profile.name!r} 的 backend {profile.backend!r} 未注册"
            f"（已注册：{sorted(_llm_names())}）。"
        )
    llm = _llm_defaults(profile.backend)
    base_url = llm.get("base_url") or llm.get("default_base_url")
    # langchain-ollama 工厂需要 model；其余只需 base_url
    if profile.backend == "langchain-ollama":
        return _llm_create(profile.backend, model=profile.model, base_url=base_url)
    return _llm_create(profile.backend, base_url=base_url)


def _llm_names():
    from .llm import names
    return names()


def build_reason(profile: AgentProfile, *, backend=None) -> ReasonProvider:
    """按角色构造一个 LlmReasonProvider（模型连接 + 模型名 + system_prompt + 温度）。

    返回的 reason 未施加能力域；如需限定该 agent 可呼叫范围，用 ``scoped()`` 包一层。
    """
    from .kernel.agent import LlmReasonProvider
    backend_obj = backend if backend is not None else resolve_backend(profile)
    return LlmReasonProvider(
        backend_obj,
        model=profile.model,
        system_prompt=profile.system_prompt,
        temperature=profile.temperature if profile.temperature is not None else 0.2,
    )


class ScopedReason(ReasonProvider):
    """能力域包装：把某 Agent 的可呼叫清单在交给推理前按下发白名单过滤。

    让"不同 Agent 不仅 prompt 不同、可点名范围也不同"。只读包一层，不改底层 reason。
    """

    def __init__(self, profile: AgentProfile, inner: ReasonProvider) -> None:
        self._profile = profile
        self._inner = inner
        self._patterns = profile.capabilities

    async def decide(self, ctx: Scope) -> Action:
        if self._patterns:
            import fnmatch
            allowed = [c for c in ctx.capabilities
                       if any(fnmatch.fnmatch(c.get("target", ""), p) for p in self._patterns)]
            ctx.capabilities = allowed
        return await self._inner.decide(ctx)


def scoped(profile: AgentProfile, reason: ReasonProvider) -> ReasonProvider:
    """若 profile 声明了 abilities 白名单则包 ScopedReason，否则原样返回。"""
    if not profile.capabilities:
        return reason
    return ScopedReason(profile, reason)


# ---------------------------------------------------------------
# 组装：按 kind 生成内核实体
# ---------------------------------------------------------------

def build_kernel(
    profile: AgentProfile,
    bus,
    *,
    sub_factory=None,
    max_concurrency: Optional[int] = None,
    reason: Optional[ReasonProvider] = None,
    input_nodes: Optional[list] = None,
):
    """按 role.kind 把角色装配成对应内核实体。

    - worker/general -> 返回已包能力域的 ``ReasonProvider``（供 AgentLoop/子 agent 用）；
    - kernel         -> 返回组装好的 ``KernelAgent``（sub_factory 用 profile.children 未落地，需注入）；
    - resident       -> 返回 ``ResidentKernel``（并发上限为框架层参数，由调用方传入）。
    具体 kind 的完整内核装配仍需调用方提供 bus 与既有能力/横切面；本函数负责
    把"用哪个模型 + 什么 prompt + 可呼叫范围"这套角色化绑定接进去。
    """
    base = reason if reason is not None else build_reason(profile)
    bounded = scoped(profile, base)
    nodes = _resolve_input_nodes(profile, input_nodes)

    if profile.kind == "kernel":
        from .kernel.agent import KernelAgent
        fac = sub_factory or (lambda goal, agent_id: None)
        return KernelAgent(
            bus,
            sub_factory=fac,
            input_nodes=nodes,
        )
    if profile.kind == "resident":
        from .kernel.agent import ResidentKernel
        return ResidentKernel(
            bus,
            bounded,
            max_concurrency=max_concurrency or 4,
            input_nodes=nodes,
        )
    # worker / general：返回一个可直接驱动 AgentLoop 的能力域 reason
    return bounded


def _resolve_input_nodes(profile: AgentProfile, override: Optional[list]):
    if override is not None:
        return override
    if profile.input_nodes:
        return list(profile.input_nodes)
    return None


__all__ = [
    "AgentProfile",
    "load_profiles",
    "load_raw",
    "build_reason",
    "build_kernel",
    "resolve_backend",
    "ScopedReason",
    "scoped",
]
