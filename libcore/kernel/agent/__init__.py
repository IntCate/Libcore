"""kernel/agent/ —— 决策层：调度环 + 决策 SPI + LLM 决策者 + 常驻内核。

统一导出本子包的公开符号。外部一律经 ``libcore.kernel.agent`` 引入，
不要直接引用 ``libcore.kernel.agent.loop`` / ``spi`` / ``lm_reason`` / ``resident`` 内部模块。
LLM backend 已独立至中立库 ``libcore.llm``，不再隶属于 kernel/agent/。
"""
from .loop import AgentLoop
from .spi import Action, ReasonProvider
from .lm_reason import LlmReasonProvider
from .resident import ResidentKernel
from .subagent import SubAgent, KernelAgent

__all__ = [
    "AgentLoop",
    "Action",
    "ReasonProvider",
    "LlmReasonProvider",
    "ResidentKernel",
    "SubAgent",
    "KernelAgent",
]
