"""libroce 开箱即用高层 API —— 普通开发者不需要懂"适配器/backend/装配"。

用法（这就是开发者要写的全部）：
    from libroce.api import Agent

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

from .agent.loop import AgentLoop
from .agent.lm_reason import LlmReasonProvider
from .core.bus import EventBus
from .core.event import CapabilityResult

# ---------------------------------------------------------------
# 内置 backend 注册表：字符串 -> 工厂。高级用户可以 register_backend 加自己的。
# 普通开发者完全不用关心这层。
# ---------------------------------------------------------------
_BACKENDS: Dict[str, Callable[[], Any]] = {}


def register_backend(name: str, factory: Callable[[], Any]) -> None:
    """（高级）注册一个新的 backend 工厂，之后 Agent(backend=name) 就能用。"""
    _BACKENDS[name] = factory


def _ensure_defaults() -> None:
    if _BACKENDS:
        return

    from .agent.backends.ollama import OllamaBackend

    def ollama(base_url: str = "http://localhost:11434") -> Any:
        return OllamaBackend(base_url)

    register_backend("ollama", ollama)

    try:  # langchain 未装则跳过，不强依赖
        from .agent.backends.langchain import LangChainOllamaBackend

        def langchain_ollama(model: str, base: str = "http://localhost:11434") -> Any:
            return LangChainOllamaBackend(model=model, base_url=base)

        register_backend("langchain-ollama", langchain_ollama)
    except Exception:
        pass


_ensure_defaults()

_DEFAULT_SYSTEM = (
    "你是 libroce 的调度员。你只能调用给你列出的能力；"
    "根据任务选择一个要调用的能力并带好参数；"
    "所有必须做的事都做完了，就结束（不要再调用任何工具）。"
)

# ---------------------------------------------------------------
# Agent：开发者唯一要接触的类
# ---------------------------------------------------------------

class Agent:
    """一个"选好模型、注册能力、跑任务"的便捷壳。"""

    def __init__(
        self,
        *,
        backend: str = "ollama",
        model: str = "qwen3:0.6b",
        base_url: str = "http://localhost:11434",
        system_prompt: str = _DEFAULT_SYSTEM,
        temperature: float = 0.2,
    ) -> None:
        if backend not in _BACKENDS:
            raise KeyError(f"未知 backend：{backend!r}（已内置：{sorted(_BACKENDS)}）")
        self._bus = EventBus()
        factory = _BACKENDS[backend]
        # langchain-ollama 工厂需要 model；其余不需要
        backend_obj = factory(model=model, base_url=base_url) if backend == "langchain-ollama" \
            else factory(base_url=base_url)
        self._reason = LlmReasonProvider(
            backend_obj, model=model, system_prompt=system_prompt, temperature=temperature,
        )

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
        loop = AgentLoop(self._bus, self._reason)
        return asyncio.run(loop.run({"goal": goal}))