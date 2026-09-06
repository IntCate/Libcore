"""常驻协作内核：7×24 待机 + 事件驱动唤醒 + 优雅关闭。

与 ``libcore-resident-loop-design.md`` 的"自主主循环"（Perceive→Decide→Act→Learn）
不同，本内核坚持**可控性**：

- 内核 7×24 常驻待机（asyncio 事件循环），但**不主动感知环境、不自造意图**；
- 只响应**被点名的协作请求**（``submit(goal)``），收到才用 AgentLoop 执行；
- 决策分层：目标/边界层（谁给目标）归调用方，协作解析层（怎么拆、哪个子 agent
  干什么）归内核 agent，底层网格层（EventBus/路由/横切面）归机制；
- 内核绝不越过领到的调度边界去创造新目标。

复用：EventBus + AgentLoop + ReasonProvider，零改动。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from ..bus import EventBus, Notice
from .loop import AgentLoop
from .spi import ReasonProvider

_logger = logging.getLogger("libcore.resident")


class ResidentKernel:
    """常驻协作内核：7×24 待机，事件驱动唤醒，复用 AgentLoop 执行协作请求。

    - ``serve()`` 启动常驻事件循环，阻塞等待协作请求（不轮询、不造活）；
    - ``submit(goal)`` 由外部/其他 agent 点名投递一个协作请求；
    - 每个请求用独立的 AgentLoop 执行（复用现有调度环，零改动）；
    - ``shutdown()`` 优雅关闭：停止接收新请求，排空在途任务后退出。
    """

    def __init__(
        self,
        bus: EventBus,
        reason: ReasonProvider,
        *,
        max_concurrency: int = 4,
        input_nodes: Optional[list] = None,
    ) -> None:
        self.bus = bus
        self.reason = reason
        self._input_nodes = input_nodes
        self._max_concurrency = max_concurrency
        self._queue: asyncio.Queue = asyncio.Queue()
        self._sem = asyncio.Semaphore(max_concurrency)
        self._active: set[asyncio.Task] = set()
        self._results: list[dict] = []
        self._running = False
        self._shutdown_requested = False

    # ---- 对外：协作请求入口 ----

    def submit(self, goal: Any, *, reason: ReasonProvider) -> None:
        """点名投递一个协作请求（外部/其他 agent 调用）。

        只入队、不阻塞调用方；常驻主循环取到后执行。
        关闭流程开始后拒绝新请求。

        ``reason``：必填，为该请求注入**独立的决策者实例**（子 agent 各自持有
        独立大脑，互不干扰）。不提供即报错——子 agent 必须独立大脑，不默默共享。
        """
        if self._shutdown_requested:
            raise RuntimeError("内核已进入关闭流程，不再接收新协作请求")
        self._queue.put_nowait((goal, reason))
        _logger.debug("resident.submit goal=%r reason=%s", goal, reason is not None)

    # ---- 常驻主循环 ----

    async def serve(self) -> None:
        """启动常驻主循环：阻塞等待协作请求，收到即用 AgentLoop 执行。

        7×24 待机：没有请求时 ``await self._queue.get()`` 挂起，不占 CPU、不轮询。
        """
        self._running = True
        await self.bus.publish(Notice(topic="resident.started", payload={}))
        _logger.info("resident kernel started (7x24)")
        try:
            while self._running:
                item = await self._queue.get()   # 事件驱动唤醒：阻塞等待协作请求
                if item is None:                 # 关闭哨兵
                    self._queue.task_done()
                    break
                goal, reason = item
                self._spawn(goal, reason)
                self._queue.task_done()
        finally:
            await self._drain()
            await self.bus.publish(Notice(topic="resident.stopped", payload={}))
            _logger.info("resident kernel stopped")

    def _spawn(self, goal: Any, reason: ReasonProvider | None) -> None:
        """为协作请求创建执行任务（并发上限由 _execute 内信号量控制，主循环不阻塞）。"""
        task = asyncio.create_task(self._execute(goal, reason))
        self._active.add(task)
        task.add_done_callback(self._on_done)

    async def _execute(self, goal: Any, reason: ReasonProvider) -> None:
        """用独立 AgentLoop 执行一个协作请求（受并发上限约束）。

        ``reason``：该请求自己的决策者实例（子 agent 独立大脑，必填）。
        """
        async with self._sem:
            loop = AgentLoop(self.bus, reason, input_nodes=self._input_nodes)
            try:
                ctx = await loop.run(goal)
                self._results.append({
                    "goal": goal,
                    "done": ctx.done,
                    "steps": len(ctx.observations),
                    "observations": list(ctx.observations),
                })
                await self.bus.publish(Notice(topic="resident.completed",
                                              payload={"goal": goal, "done": ctx.done}))
            except Exception as e:  # noqa: BLE001 - 单个请求失败不拖垮常驻内核
                _logger.error("resident.execute error goal=%r err=%s", goal, e)
                await self.bus.publish(Notice(topic="resident.failed",
                                              payload={"goal": goal, "error": str(e)}))

    def _on_done(self, task: asyncio.Task) -> None:
        self._active.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _logger.error("resident task error: %s", exc)

    # ---- 优雅关闭 ----

    async def shutdown(self) -> None:
        """优雅关闭：停止接收新请求，唤醒主循环，排空在途任务后退出。"""
        self._shutdown_requested = True
        self._running = False
        self._queue.put_nowait(None)   # 唤醒可能阻塞在 queue.get() 的主循环
        if self._active:
            await asyncio.gather(*self._active, return_exceptions=True)

    async def _drain(self) -> None:
        """排空队列中尚未处理的请求（关闭时丢弃，符合"停止接收"语义）。"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

    # ---- 状态 ----

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def results(self) -> list[dict]:
        return list(self._results)
