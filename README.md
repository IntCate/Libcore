# libroce — 事件网格（Mesh）工程 · 参考实现

> **libroce 是「Mesh（事件网格）工程」范式的一个最小参考实现。**
> 本 README 先讲清楚 *什么是 Mesh 工程*（范式定名与定性），再说明 *libroce 如何落地它*。

---

## 一、什么是 Mesh 工程

### 1.1 为什么需要一个新范式

业界 agentic 架构目前几乎只有两个形态，且**被逼着二选一**：

- **Graph（图工程）**：预先画好节点与边（A→B→C）。受控、可预测、可审批；但僵化，改流程要改图。
- **Agentic Loop（循环工程）**：一个模型围着它自己的上下文自我循环。自由、动态；但横切能力（限流/鉴权/trace）难插、多 agent 难协作、自迭代没有天然数据收口。

两个范式都缺一张**"信号的一等公民通道"**：消息藏在函数调用里、横切逻辑只能靠套壳、运行轨迹没有统一着落点。Mesh 就是为补上这一块而提出的第三形态。

### 1.2 Mesh 的定义（一句话）

> **以 "事件总线（Mesh）" 为数据面与通讯面，以 "运行时决策" 为边的生长机制，使 Graph 与 Loop 在同一基底上共存并可互相折叠的编排范式。**

展开是三句：

1. **网是一等公民**：所有信号（点名 `Dispatch`、广播 `Notice`）都流经事件总线。因此它可以被拦截（横切面）、被回放、被采集（自迭代）、供多 agent 互点。
2. **边由运行时决策浮现**：没有预先画死的执行图。"下一步连到哪里"由 Agent（或其他决策者）在运行中决定；点名的目标可以是任何挂在网上的能力（工具 / 存储 / 子 Agent / 记忆）。
3. **轨迹 ⟷ 工作流可折叠**：Agent 自由跑一圈留下的 `target` 序列轨迹，可冻结成一张受控的 Graph（工作流）；一张 Graph 也可在运行时散开成动态的 Loop。**Graph 不是另一种内核，而是"被验证过的动态决策的固化成形"。**

### 1.3 三条独有特征（判据：别人表达不了的能力）

| # | Mesh 独有能力 | 传统 Graph | 传统 Loop |
|---|---|---|---|
| 1 | 事件总线作为一等公民通道（可拦 / 可回放 / 可采集 / 多节点互点） | 无 | 无 |
| 2 | Graph 与 Loop 由同一执行原语承载、可互相折叠 | 只有图 | 只有循环 |
| 3 | 全量轨迹天然收口于总线，直接成为自迭代 / 工作流导入的数据源 | 无统一收口 | 无统一收口 |

凭以上三点，Mesh 不是 Graph 的变体，也不是 Loop 的变体，而是**同时承载两者的统一调度范式**，够格独立成派。

### 1.4 与已知范式对比

| 维度 | Graph | Agentic Loop | **Mesh（本范式）** |
|---|---|---|---|
| 下一跳的边 | 预先画死 | 模型逐轮自选 | **决策 + 总线路由，两者皆可** |
| 信号通道 | 函数调用 | 函数调用 | **事件总线（一等公民）** |
| 横切能力 | 图中间件 | 套壳/手写 | **总线拦截层（可插拔）** |
| 多 Agent | 图节点 | 各开各的循环 | **同一网上互点** |
| 工作流 | 原生（图即工作流） | 靠手写 | **轨迹冻结即工作流** |
| 自迭代数据 | 无统一收口 | 无 | **轨迹即数据源** |

### 1.5 定性

> **Mesh（事件网格）工程 = 以事件总线为网、以运行时决策为边的生长期范式。** Graph 与 Loop 是网上两种边的形态——预定义（受控 / 可审批）与运行时浮现（自由 / 自愈），二者通过"轨迹冻结 / 图散开"互相折叠。它同时提供**可控性**（工作流入口）与**自由度**（动态决策入口），不必二选一。

---

## 二、libroce：Mesh 的最小落地

libroce 是上述范式的一个**最小可运行参考实现**，专注把"Mesh 的核心"跑通并保持薄。

### 2.1 设计立场

- **内核极薄**：没有业务逻辑，内核只解决"通讯 + 决策 + 闭环"。
- **`emit + listen` 只负责"通讯"，不负责"决策"**：决策交给挂在网上的决策者（Agent）。
- **组件可替换**：能力、横切面、后端、存储都以 SPI 接入，可独立热更，不动内核。
- **自迭代成自然**：全量轨迹都在总线上，回灌即训练数据。

### 2.2 架构分层

```
高层 API        libroce.api.Agent          —— 开箱即用（普通开发者入口）
                -------------
参考接入        agent/lm_reason            —— 把 LLM 接成决策者（工具调用即点名）
                agent/backends             —— 各 LLM 后端适配（官方 ollama / langchain …）
                -------------
决策与调度      agent/spi                  —— 唯一定义：ReasonProvider.decide(ctx)->Action
                agent/loop                 —— 闭环：reason → dispatch → observe
                -------------
内核            core/bus                   —— 事件总线：点名直投 + 广播 + 横切面 + manifest
                core/event                 —— 信号原语：Dispatch / Notice / CorrelationId / CapabilityResult
                core/scope                 —— 会话上下文（白板 + 清单 + 因果链）
                -------------
插件            plugins/loader             —— 自发现 + 配置=唯一真相源 + 文件驱动热更新
```

### 2.3 三个 Action 原语（决策者的"答题卡"）

```python
class Action:
    target: str      # 点名谁（挂在网上的能力）
    op: str          # 调它的哪个操作
    payload: dict    # 参数
    # 或二选一：
    finish = True    # "做完了"，结束本轮
    wait   = True    # "本轮无目标"，回到轮顶刷新清单
```

### 2.4 能力清单（manifest）注入

每轮循环开始，内核把**最新可调用清单**注入决策上下文 `ctx.capabilities`。Agent 只能点清单里有的；点名不存在 → 总线抛 `NoHandlerError`，当场现形。加/换插件，Agent 下一轮自动看见——这就是"架构自迭代"的第一环。

### 2.5 横切面（原来的 harness 壳，如今拦截信号）

限流 / 鉴权 / trace / 呼叫重试 / 沙箱等不再是"包住 Agent 的壳"，而是**穿在总线信号路径上的横切面插件**（Aspect），按信号或目标匹配，可插拔、可排序、可裁剪。

### 2.6 双层 API

- **普通路径（开箱即用）**：开发者只需"选模型 + 注册能力 + 跑"，完全不用碰适配器 / 装配。

```python
from libroce.api import Agent

agent = Agent(backend="ollama", model="qwen3:0.6b")

@agent.capability("cap.now", description="返回当前时间")
def now(payload):
    import datetime
    return {"now": datetime.datetime.now().isoformat(timespec="seconds")}

result = agent.run("获取当前时间")   # 剩下框架跑
```

- **高级路径**：自定义 `ReasonProvider.decide`，或用任意库写 backend（官方 / langchain / 自研），要接去哪就接哪。

### 2.7 配置 = 唯一真相源 + 热更新

```yaml
plugins:
  - name: pdf_tool
    enabled: true
    description: 解析 PDF 提取文本与分块
```

- 自发现把目录里的插件**写回**配置文件；框架**读**配置加载；Agent 经 manifest **看**配置；你直接**编辑**配置。一份顶三份，无双真相源。
- `watch()` 监听文件变化 → 运行时 `on/off` + 广播 → Agent 自动发现（文件驱动热更新）。

### 2.8 目录结构

```
backend/libroce/
├── api.py                  # 高层开箱即用 Agent
├── kernel.py               # 便捷装配
├── core/                   # 内核（极薄）
│   ├── bus.py              #   事件总线
│   ├── event.py            #   信号原语
│   └── scope.py            #   会话作用域
├── agent/                  # 决策与闭环
│   ├── spi.py              #   ReasonProvider / Action
│   ├── loop.py             #   调度环
│   ├── lm_reason.py        #   LLM 决策者
│   └── backends/           #   LLM 后端适配
├── plugins/                # 插件子系统
│   └── loader.py           #   自发现 + 配置真相源 + 热更新
├── demo.py                 # 内核运行演示
├── demo_llm_ollama.py      # 适配器通用性验证
├── demo_migrate_file.py    # 真实能力迁移试点
└── demo_plugins/           # 示例插件
```

### 2.9 运行与验证

```bash
cd backend
python -m libroce.demo                 # 内核：点名/清单/横切面/护栏/热更新/插件自发现
python -m libroce.demo_llm_ollama      # 用你真机 ollama 验证适配器通用性
python -m libroce.demo_migrate_file    # 真实能力迁移试点（旧 FileReadTool → 总线能力）
python -m pytest tests/libroce -q      # 自动化测试
```

### 2.10 真实能力迁移（薄壳走法）

旧 `tool_kernel` 的 `IBuiltinTool.execute(args, ctx)` → 约 15 行薄壳包成总线 `handler(payload)`，**业务逻辑零重写**：

```python
async def adapt_builtin(tool):
    async def handler(dispatch):
        data = await tool.execute(dispatch.payload, ctx())   # 复用旧工具
        return CapabilityResult(ok=not _is_error(data), data=data)
    return handler
```

已在试点中验证：旧文件读取工具被新内核真实驱动、读到真实文件全文。迁移建议用**短层级名**（`file.read`）作 `target`，长 `component_id` 保留为元数据。

---

## 三、状态

- **Milestone**：M0 — 最小可运行内核原型（受自动化测试保护）。
- **已验证理念**：Agent 唯一决策、点名直投（消灭隐性流程）、横切面为信号插件、护栏为内核一部分、全量轨迹为自迭代数据源、配置唯一真相源 + 文件驱动热更新、双层 API、真实旧能力可迁移。
- **测试**：libroce 测试套件全绿（`tests/libroce/`）。
- **当前边界**：仍是骨架——决策策略默认"工具调用即点名"，尚无"经验库/自迭代回灌"实装；能力以演示级为主；无持久化与真实外部 API 入口。

---

*作者注：本 README 第一部分为范式论文（Mesh 工程定名与定性），第二部分为 libroce 参考实现说明。两者可分读。*