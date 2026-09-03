"""事件总线：点名直投 + 广播感知 + 横切面管。

核心法则：总线的职责止步于"投递"，决策的职责止步于 Agent。
总线不判断"下一步该干嘛"，它只负责：
- 点名（Dispatch）：按 target 定向查表直投，不做谓词匹配（消灭隐性流程）；
- 广播（Notice）：按 topic 扇出，用于系统级感知；
- 横切面（Aspect）：对所有流过的信号统一拦截（日志 / 护栏 / 追踪）。
"""
from __future__ import annotations

import inspect
from abc import ABC
from typing import Any, Callable, Dict, List, Union

from .event import NoHandlerError, Dispatch, Notice, CapabilityResult

Handler = Callable[[Dispatch], Any]
Signal = Union[Dispatch, Notice]


def _is_awaitable(value: Any) -> bool:
    """容忍同步 handler 直接返回结果，异步 handler 返回协程。"""
    return inspect.isawaitable(value)


class Aspect(ABC):
    """横切面插件 SPI：包围所有流经总线的信号，不参与调度、不决定下一步。"""

    def matches(self, signal: Signal) -> bool:
        """过滤条件：默认匹配全部信号，子类可只关心特定 target / topic。"""
        return True

    async def before(self, signal: Signal) -> None:  # noqa: B027 - 可空实现
        """投递前阶段（护栏 / 日志 / 埋点）。"""

    async def after(self, signal: Signal, result: Any) -> None:  # noqa: B027
        """投递后阶段（追踪 / 结果归一）。"""


class EventBus:
    """内核唯一的统一信号通道。所有节点（含 Agent）都挂在这个总线上。"""

    def __init__(self) -> None:
        self._handlers: Dict[str, Handler] = {}        # 点名表（O(1) 直投）
        self._metas: Dict[str, dict] = {}              # 能力清单元数据（描述/schema）
        self._subs: Dict[str, List[Handler]] = {}      # 广播表（系统通知）
        self._aspects: List[Aspect] = []               # 横切面管

    # ---- 注册（装配，运行期可重新调用以热更新）----

    def on(self, target: str, handler: Handler, meta: dict | None = None) -> None:
        """注册能力：进点名表。同名 target 覆盖（避免重复注册静默异常）。

        ``meta`` 用于登记能力"被发现"所需的描述与 schema，随后经
        ``manifest()`` 暴露给 Agent 作为可呼叫清单。
        """
        self._handlers[target] = handler
        if meta is not None:
            self._metas[target] = dict(meta)

    def off(self, target: str) -> None:
        """注销能力：从点名表与清单中移除（热更新下线）。"""
        self._handlers.pop(target, None)
        self._metas.pop(target, None)

    def manifest(self) -> List[dict]:
        """生成可呼叫清单：``{target, ...meta}`` 的可读投影。

        Agent 决策时据此知道"能呼叫什么"。运行期动态返回最新一份。
        """
        return [{"target": t, **self._metas.get(t, {})} for t in self._handlers]

    def subscribe(self, topic: str, handler: Handler) -> None:
        """订阅广播主题（系统级通知 / 感知）。"""
        self._subs.setdefault(topic, []).append(handler)

    def add_aspect(self, aspect: Aspect) -> None:
        """挂载横切面插件。顺序 = 挂载顺序（before 正序 / after 逆序）。"""
        self._aspects.append(aspect)

    def remove_aspect(self, aspect: Aspect) -> None:
        """卸载横切面插件（热更新重建横切管时使用）。"""
        try:
            self._aspects.remove(aspect)
        except ValueError:
            pass

    # ---- 投递 ----

    async def dispatch(self, d: Dispatch) -> CapabilityResult:
        """点名投递：按 target 定向直投到唯一 handler。

        兼容同步与异步 handler：返回值若是 awaitable 则自动 await。
        """
        handler = self._handlers.get(d.target)
        if handler is None:
            raise NoHandlerError(d.target)
        await self._run_aspects("before", d)
        result = handler(d)
        if _is_awaitable(result):
            result = await result
        await self._run_aspects("after", d, result)
        return result

    async def publish(self, n: Notice) -> None:
        """广播投递：按 topic 扇出到所有订阅者（与 dispatch 一样兼容同步/异步）。"""
        await self._run_aspects("before", n)
        for handler in self._subs.get(n.topic, ()):
            result = handler(n)
            if _is_awaitable(result):
                await result
        await self._run_aspects("after", n, None)

    # ---- 内部 ----

    async def _run_aspects(self, phase: str, signal: Signal, result: Any = None) -> None:
        chain = self._aspects if phase == "before" else list(reversed(self._aspects))
        for aspect in chain:
            if not aspect.matches(signal):
                continue
            step = getattr(aspect, phase)
            if phase == "before":
                await step(signal)
            else:
                await step(signal, result)