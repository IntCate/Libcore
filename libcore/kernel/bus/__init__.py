"""kernel/bus/ —— 传输层：事件原语 + 事件总线 + 上下文作用域。

统一导出本子包的公开符号。外部一律经 ``libcore.kernel.bus`` 引入，
不要直接引用 ``libcore.kernel.bus.event`` / ``bus.py`` / ``scope`` 内部模块。
"""
from .bus import EventBus, Aspect, Handler, Signal
from .event import (
    CorrelationId,
    Dispatch,
    Notice,
    CapabilityResult,
    NoHandlerError,
)
from .scope import Scope

__all__ = [
    "EventBus",
    "Aspect",
    "Handler",
    "Signal",
    "CorrelationId",
    "Dispatch",
    "Notice",
    "CapabilityResult",
    "NoHandlerError",
    "Scope",
]
