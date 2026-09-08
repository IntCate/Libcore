"""Agent 角色配置测试：agents.yaml 加载 / 校验 / 能力域过滤。

模型连接默认走配置（ollama），不要求本机真正运行 ollama——
本文件只测「配置解析」与「能力域过滤」两层，不触发真实推理。
"""
from __future__ import annotations

import asyncio

from libcore.kernel.bus import Dispatch, CapabilityResult
from libcore.kernel.agent import LlmReasonProvider
from libcore import agents as agents_mod
from libcore.agents import load_profiles, scoped, AgentProfile


def _dispatch(bus, target, op="run", payload=None):
    return Dispatch(target=target, op=op, payload=payload or {})


class _FakeChatBackend:
    """假 backend：不真连模型，只回一个固定工具调用，用于驱动 ScopedReason。"""

    def __init__(self):
        self.last_options = None

    async def chat(self, *, model, messages, options=None):
        self.last_options = options
        return _ToolResult([_ToolCall("banned.cap", {"op": "run"})])


def _tool_result(tool_calls):
    class R:
        tool_calls = tool_calls
    return R()


class _ToolCall:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolResult:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


class TestLoadProfiles:
    def test_loads_sample_roles(self):
        profiles = load_profiles()
        # 示例 agents.yaml 里定义了这 4 个角色
        for role in ("kernel", "worker", "coder", "resident"):
            assert role in profiles, f"缺角色 {role}"

    def test_prompt_not_empty_for_every_role(self):
        profiles = load_profiles()
        for name, p in profiles.items():
            assert p.system_prompt.strip(), f"角色 {name} 没有 system_prompt"

    def test_worker_uses_default_temperature(self):
        p = load_profiles()["worker"]
        assert p.kind == "worker"
        assert p.temperature == 0.1  # 该角色显式设置了 temperature

    def test_unknown_role_absent(self):
        profiles = load_profiles()
        assert "not_a_role" not in profiles

    def test_coder_capabilities_parsed_as_list(self):
        """coder 的 capabilities 是 dict{allow:[...]}，加载后必须归一化为 list。"""
        p = load_profiles()["coder"]
        assert isinstance(p.capabilities, list), (
            f"coder.capabilities 应为 list，实际 {type(p.capabilities).__name__}: {p.capabilities!r}"
        )
        assert p.capabilities == ["tools", "tool.*", "skill"]


class TestScopedReason:
    def test_filters_capability_whitelist(self):
        """ScopedReason 只把白名单匹配的 target 交给底层 reason。"""
        inner = LlmReasonProvider(_FakeChatBackend(), model="x",
                                  system_prompt="s", temperature=0.2)
        profile = AgentProfile(
            name="coder", kind="worker", system_prompt="s",
            model="x", capabilities=["tools", "tool.*"],
        )
        bounded = scoped(profile, inner)
        assert isinstance(bounded, agents_mod.ScopedReason)

        # 用真实 Scope 注入含越界 target 的可呼叫清单
        from libcore.kernel.bus import Scope
        ctx = Scope(goal="demo")
        ctx.capabilities = [{"target": t} for t in ("tools", "banned.cap", "tool.file")]
        asyncio.run(bounded.decide(ctx))
        # 白名单 tools / tool.* 过滤后只剩这两项，越界的 banned.cap 被剔除
        assert {c["target"] for c in ctx.capabilities} == {"tools", "tool.file"}

    def test_no_whitelist_returns_inner(self):
        inner = object()
        profile = AgentProfile(name="w", kind="worker", system_prompt="s", model="x")
        assert scoped(profile, inner) is inner  # 未配 allow -> 原样返回，不包层


def test_module_importable():
    """确保 agents.py 与 api.by_profile 至少可导入、无语法/引用错。"""
    import libcore.api as api
    assert hasattr(api.Agent, "by_profile")
    assert callable(api.Agent.by_profile)


class TestBudgetGuardConfig:
    """横切面护栏：budget_guard 的 max_iterations 可由配置注入（帧迭代上限归横切面）。"""

    def test_register_accepts_max_iterations(self):
        from libcore.plugins.aspects import budget_guard
        bus = _NewEventBus()
        budget_guard.register(bus, max_iterations=42)
        guard = bus._aspects[0]
        assert guard.max == 42

    def test_register_default_is_25(self):
        from libcore.plugins.aspects import budget_guard
        bus = _NewEventBus()
        budget_guard.register(bus)  # 缺省
        guard = bus._aspects[0]
        assert guard.max == 25


class _NewEventBus:
    """最小总线替身：只暴露 add_aspect 以承载 budget_guard 注册。"""

    def __init__(self):
        self._aspects = []

    def add_aspect(self, aspect):
        self._aspects.append(aspect)

