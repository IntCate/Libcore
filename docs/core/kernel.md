# 内核（决策层）

> 从零开始理解 libcore 的决策层：AgentLoop 调度环、ReasonProvider 决策 SPI、ResidentKernel 常驻内核。

## 1. 内核是什么

内核是 agent 的"大脑 + 调度器"。它不执行具体业务，而是负责：

- **读输入**：每轮决策前，从配置驱动的输入节点（context/prompt/memory…）取回上下文。

- **决策**：调用 ReasonProvider（通常是 LLM）推理出"下一步点名谁、带什么参数"。

- **调度**：把决策转成 Dispatch 发到总线，观察结果，进入下一轮。

- **常驻**：7×24 待机，事件驱动唤醒，复用 AgentLoop 执行协作请求。

## 2. 三个核心组件

| 组件                 | 位置                         | 职责                                  |
| ------------------ | -------------------------- | ----------------------------------- |
| **AgentLoop**      | `kernel/agent/loop.py`     | 调度环：reason → dispatch → observe 的闭环 |
| **ReasonProvider** | `kernel/agent/spi.py`      | 决策 SPI：给定上下文，返回下一步 Action           |
| **ResidentKernel** | `kernel/agent/resident.py` | 常驻内核：7×24 待机 + 事件驱动唤醒               |

## 3. AgentLoop：调度环

核心循环：

```python
while not ctx.done:
    ctx.refresh(bus.manifest())      # 每轮注入最新可呼叫清单
    await self._prepare_input(ctx)   # 决策前无感注入 prompt/context 强化片段
    action = await self.reason.decide(ctx)   # 决策：下一步点名谁
    await bus.publish(Notice("loop.iteration", ...))  # 治理横切面维护跨轮次状态
    if action.finish: break           # 决策者宣告结束
    if action.wait: continue         # 本轮无目标，稍等后回到轮顶
    result = await bus.dispatch(Dispatch(target=action.target, op=action.op, ...))
    ctx.observe(result)              # 观察结果，进入下一轮
```

### 决策输入节点（配置驱动）

`input_nodes` 是"决策输入节点"配置驱动的 target 列表：

- 内核**不硬编码**任何具体输入节点名，只负责在每轮决策前逐个调用、无感降级并聚合。

- 默认 `[{"target":"context","slot":"user"}, {"target":"prompt","slot":"system"}]`。

- 可配置为任意节点（如加 `memory`）。**加一个输入节点只改配置/传参，内核零改动。**

```python
AgentLoop(bus, reason, input_nodes=[
    {"target": "context", "slot": "user"},
    {"target": "prompt", "slot": "system"},
    {"target": "memory", "slot": "user"},   # 加一个输入节点
])
```

### 护栏

- **迭代上限**：由治理横切面 `budget_guard` 承担（aspects.yaml 的 `max_iterations`），达成 → 标记 done，广播 `loop.guard`。

- **等待超时**：`action.wait` 连续 50 次 → 标记 done，广播 `loop.guard`。

- 每轮广播 `loop.iteration`，供治理横切面（预算/死循环）维护跨轮次状态。

## 4. ReasonProvider：决策 SPI

```python
class ReasonProvider(ABC):
    async def decide(self, ctx) -> Action: ...
```

`Action` 是决策者的输出：

```python
@dataclass
class Action:
    target: str = ""        # 下一步点名谁
    op: str = ""            # 操作
    payload: dict = ...     # 参数
    finish: bool = False    # 宣告结束
    wait: bool = False      # 本轮无目标，稍等
```

### 内置实现：LlmReasonProvider

`LlmReasonProvider` 用 LLM 做决策。它把 `ctx`（goal + observations + manifest）组装成消息序列，交给 LLM backend，解析出 Action。

```python
reason = LlmReasonProvider(backend_obj, model="qwen3:0.6b",
                            system_prompt="你是 libcore 的调度员...", temperature=0.2)
```

## 5. ResidentKernel：常驻内核

7×24 待机 + 事件驱动唤醒 + 优雅关闭。

### 关键设计

- **不主动感知环境、不自造意图**：只响应被点名的协作请求（`submit(goal)`）。

- **决策分层**：

  - 目标/边界层（谁给目标）→ 调用方

  - 协作解析层（怎么拆、哪个子 agent 干什么）→ 内核 agent

  - 底层网格层（EventBus/路由/横切面）→ 机制

- **内核绝不越过领到的调度边界去创造新目标**。

### 用法

```python
kernel = ResidentKernel(bus, reason, max_concurrency=4)
await kernel.serve()          # 常驻待机（阻塞等待协作请求）
kernel.submit("一个目标", reason=sub_reason)   # 任意时候点名投递
await kernel.shutdown()       # 优雅关闭
```

### 并发与隔离

- `max_concurrency`：并发上限（信号量控制）。

- 每个请求用**独立的 AgentLoop** 执行。

- `reason` 必填：为该请求注入**独立的决策者实例**（子 agent 各自持有独立大脑，互不干扰）。

### 生命周期广播

| 事件                   | 时机       |
| -------------------- | -------- |
| `resident.started`   | 主循环启动    |
| `resident.completed` | 一个请求执行完成 |
| `resident.failed`    | 一个请求执行失败 |
| `resident.stopped`   | 主循环停止    |

## 6. Kernel：组合根

`libcore/kernel/__init__.py` 的 `Kernel` 类是可运行组合根，提供便捷装配：

```python
kernel = Kernel.bootstrap(
    targets={"skill": (skill_handler, {"description": "..."})},
    aspects=[PermissionAspect()],
    reason=reason,
)
await kernel.run(goal)        # 单次任务

# 或常驻
kernel = Kernel.bootstrap_resident(targets=..., aspects=..., reason=...)
await kernel.serve()
kernel.submit(goal, reason=sub_reason)
await kernel.shutdown()
```

## 7. 代码位置

- `libcore/kernel/agent/loop.py`：AgentLoop

- `libcore/kernel/agent/spi.py`：Action / ReasonProvider

- `libcore/kernel/agent/lm_reason.py`：LlmReasonProvider

- `libcore/kernel/agent/resident.py`：ResidentKernel

- `libcore/kernel/agent/subagent.py`：SubAgent / KernelAgent

- `libcore/kernel/__init__.py`：Kernel 组合根

