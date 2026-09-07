"""libcore 开箱即用高层 API —— 普通开发者不需要懂"适配器/backend/装配"。

用法（这就是开发者要写的全部）：
    from libcore.api import Agent

    agent = Agent(backend="ollama", model="qwen3:0.6b")   # 只要选模型，适配器框架替你搭

    @agent.capability("cap.now", description="返回当前时间")  # 你只需"注册一个能力"
    def now(payload):
        import datetime
        return {"now": datetime.datetime.now().isoformat(timespec="seconds")}

    result = agent.run("获取当前时间")                        # 剩下框架跑

底层仍复用：内置 backend 注册表 + LlmReasonProvider + AgentLoop。
想要更细控制的高级用户仍可回到 agent/lm_reason.py 那一层自定义 backend。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict

from .kernel.bus import EventBus, CapabilityResult
from .kernel.agent import AgentLoop, LlmReasonProvider, ResidentKernel
from .llm import (
    has as _llm_has,
    names as _llm_names,
    create as _llm_create,
    defaults as _llm_defaults,
)


def register_backend(name: str, factory) -> None:
    """（高级）注册一个新的 backend 工厂，之后 Agent(backend=name) 就能用。"""
    from .llm import register

    return register(name, factory)

# ---------------------------------------------------------------
# Agent：开发者唯一要接触的类
# ---------------------------------------------------------------

class Agent:
    """一个"选好模型、注册能力、跑任务"的便捷壳。"""

    def __init__(
        self,
        *,
        backend: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
    ) -> None:
        _d = _llm_defaults()
        backend = backend or _d.get("default_backend", "ollama")
        model = model or _d.get("default_model", "qwen3:0.6b")
        base_url = base_url or _d.get("default_base_url") or "http://localhost:11434"
        temperature = _d.get("temperature", 0.2) if temperature is None else temperature
        # 系统指令唯一真相源 = 配置 llm.yaml system_prompt（缺省回退 llm 包内建兜底）
        if system_prompt is None:
            system_prompt = _d.get("system_prompt") or ""
        if not _llm_has(backend):
            raise KeyError(f"未知 backend：{backend!r}（已内置：{sorted(_llm_names())}）")
        self._bus = EventBus()
        # langchain-ollama 工厂需要 model；其余不需要
        backend_obj = _llm_create(backend, model=model, base_url=base_url) if backend == "langchain-ollama" \
            else _llm_create(backend, base_url=base_url)
        self._reason = LlmReasonProvider(
            backend_obj, model=model, system_prompt=system_prompt, temperature=temperature,
        )

    @classmethod
    def by_profile(cls, role: str, **overrides):
        """按 agents.yaml 的【角色】装配一个 Agent（模型连接/prompt/能力域全部配置驱动）。

        ``role`` 是 agents.yaml 中 agents 的一个键（如 "worker"、"coder"）。
        从配置文件取该角色的 backend+model+system_prompt+temperature 交给普通构造流程，
        再按角色施加能力域过滤（可点名范围）。``overrides`` 可覆盖角色里的任何字段。
        角色不存在或 backend 未注册 -> 抛 KeyError。
        """
        from .agents import load_profiles, scoped

        profile = load_profiles().get(role)
        if profile is None:
            raise KeyError(f"未知 agent 角色：{role!r}（agents.yaml 已定义：{sorted(load_profiles())}）")
        kwargs = {
            "backend": profile.backend,
            "model": profile.model,
            "system_prompt": profile.system_prompt,
            "temperature": profile.temperature,
        }
        kwargs.update({k: v for k, v in overrides.items() if v is not None})
        self = cls(**kwargs)
        # 能力域：角色配置了 allow 白名单则把 reason 包上过滤层
        self._reason = scoped(profile, self._reason)
        return self

    def capability(self, target: str, *, description: str = ""):
        """把任意函数注册成一个"能力"，让模型能点名调用它。

        用法：作为装饰器包在能力函数上；函数接收 payload，返回值会被框架包成成功结果。
        """
        def _decorate(fn: Callable) -> Callable:
            def handler(_dispatch: Any) -> CapabilityResult:
                out = fn(_dispatch.payload)
                if isinstance(out, CapabilityResult):
                    return out
                return CapabilityResult(ok=True, data=out if out is not None else {})
            self._bus.on(target, handler, meta={"description": description})
            return fn
        return _decorate

    def run(self, goal: str):
        """跑一个任务，返回调度环的 Scope（含 observations / done）。"""
        loop = AgentLoop(self._bus, self._reason, input_nodes=self._input_nodes())
        return asyncio.run(loop.run({"goal": goal}))

    def serve(self, *, max_concurrency: int = 4, input_nodes=None):
        """以 7×24 常驻协作内核模式运行（开箱即用）。

        返回 ``ResidentKernel``；调用方自行驱动事件循环：
            kernel = agent.serve()
            await kernel.serve()          # 常驻待机（需在 asyncio 上下文）
            kernel.submit("一个目标")      # 任意时候点名投递协作请求
            await kernel.shutdown()       # 优雅关闭
        复用已注册的 bus 与 LlmReasonProvider，无需重新装配能力。
        """
        if input_nodes is None:
            input_nodes = self._input_nodes()
        return ResidentKernel(self._bus, self._reason, max_concurrency=max_concurrency, input_nodes=input_nodes)

    def channel(self, kind: str, **kwargs):
        """创建一个通道（Channel），让外部入口（UI/IM/CLI）接入本 agent 的统一通讯链路。

        返回 ``Channel`` 实例；调用方注册到 ``ChannelRegistry`` 后，入站消息经
        ``channel.ingest`` 广播，出站经 ``channel.reply`` 送回原会话。
        复用本 agent 的 bus（与已注册能力同一条总线）。

        用法：
            cli = agent.channel("cli")
            cli.ingest(InboundMessage(channel_id=cli.channel_id, kind="cli",
                                      platform="cli", user_id="u", text="你好"))
        """
        from .channels import CliChannel, ImChannel, UiChannel
        if kind == "cli":
            return CliChannel(self._bus, **kwargs)
        if kind == "ui":
            return UiChannel(self._bus, **kwargs)
        if kind == "im":
            return ImChannel(self._bus, **kwargs)
        raise ValueError(f"未知通道类型：{kind!r}（支持 cli/ui/im）")

    def serve_channels(self, *, max_concurrency: int = 4, input_nodes=None):
        """以常驻内核模式运行，并把入站广播接到内核（统一通讯链路开箱即用）。

        返回 ``ResidentKernel``（已接薄桥接）；调用方自行驱动事件循环：
            kernel = agent.serve_channels()
            await kernel.serve()          # 常驻待机
            # 任意通道 ingest 的消息都会广播 channel.inbound → 内核驱动 AgentLoop
            await kernel.shutdown()
        复用已注册的 bus 与 LlmReasonProvider，无需重新装配能力。
        """
        from .channels.bridge import wire_inbound_to_kernel
        kernel = self.serve(max_concurrency=max_concurrency, input_nodes=input_nodes)
        wire_inbound_to_kernel(self._bus, kernel)
        return kernel

    @staticmethod
    def _input_nodes():
        """从 capabilities.yaml 读取决策输入节点（含 slot）；无配置则用默认。"""
        from .plugins.loader import CapabilityLoader
        nodes = CapabilityLoader.load_input_nodes()
        return nodes or [{"target": "context", "slot": "user"},
                         {"target": "prompt", "slot": "system"}]