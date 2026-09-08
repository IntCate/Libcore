# libcore（AgentOS）生产前验收与软件工程开发体系约束

> 本文件是 libcore 的**可执行工程约束**。TRAE 主 Agent 每次开发都会读取并遵守。
> 定位：把 libcore 视为**已具备完整功能的 AgentOS**，以**开发者视角**做生产环境前的验收测试——
> 发布任务让 AgentOS 处理、逐组件核对是否符合预期、定位问题、强约束地修复与回归。
> 它定义：验收目标、功能需求矩阵、测试矩阵、问题清单、以及一套**强约束的软件工程开发体系**。

***

## 0. 角色与定位（本工作流的核心）

| 角色                | 是谁                                | 职责                                           |
| ----------------- | --------------------------------- | -------------------------------------------- |
| **开发者（主 Agent）**  | TRAE 主 Agent（当前会话）                | 以开发者视角观测 AgentOS：发布任务、核对组件行为、定位问题、强约束修复、最终验收 |
| **AgentOS（被测系统）** | libcore 框架（内核/总线/能力/横切面/通道/存储/引擎） | 被发布任务驱动，处理任务并返回可验证结果                         |
| **开发者（你）**        | 人类                                | 提供目标、拍板方向、在关键节点确认                            |

**验收闭环**：定义功能需求 → 设计测试矩阵 → 发布任务给 AgentOS → 逐组件核对 → 记录问题 → 强约束修复 → 回归全绿 → 验收。

**核心原则**：AgentOS 是**被测对象**，不是"项目 Agent 执行者"。主 Agent 直接驱动 AgentOS 的各个组件做验收，而不是把开发任务外包给 AgentOS 自己。

***

## 1. 验收目标（AgentOS 应具备什么）

AgentOS 由 **8 个子系统**构成，每个子系统有明确的应具备功能与预期行为（验收标准）：

| 子系统        | 应具备功能                                                                                                                                  | 预期行为（验收标准）                                                                                                                                               |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **内核与总线**  | EventBus（dispatch/publish/aspects）、事件模型、Scope、AgentLoop、ResidentKernel、SubAgent                                                        | dispatch 有 handler 执行并返回 CapabilityResult，无 handler 抛 NoHandlerError；publish 广播；aspects 按序执行；CorrelationId 串联因果链；AgentLoop 完成 reason→dispatch→observe 闭环 |
| **能力插件**   | skill/tools/mcp/context/prompt/memory/knowledge/api/ui/im/cli/model/**session**                                                        | 渐进式披露（manifest 只暴露真实入口，按需取详情）；find/read/run 三态；`_safe_resolve` 防路径穿越；session 收敛会话数据本体（历史+状态），通过 SessionBackend 解耦存储                                      |
| **横切面**    | permission/circuit\_breaker/sandbox/budget\_guard/loop\_governor/trace\_recorder/logging/tracing/audit/telemetry/**timeout**/**retry** | 危险命令被拦截；熔断生效；预算压力注入；loop\_governor 正确判定错误；trace\_recorder 真实记录耗时；audit 用 SHA-256 哈希链；timeout 全局超时；retry 失败重试                                             |
| **通道**     | UI/IM/CLI                                                                                                                              | 统一入站（channel.inbound 广播）与出站（Channel.reply）；三通道行为一致                                                                                                       |
| **存储**     | sqlite\_store/sqlite\_memory\_store/file\_store/state\_store                                                                           | 消息/会话/检查点/记忆完整持久化，metadata 与 importance 语义正确                                                                                                             |
| **LLM 引擎** | ollama/openai/anthropic/google/langchain/bedrock                                                                                       | 统一推理接口；多后端可切换；retry 重试；credential\_pool 凭据池                                                                                                              |
| **配置**     | capabilities/aspects/agents/llm.yaml                                                                                                   | 声明式装配；配置格式与代码解析一致（单一真相源）                                                                                                                                 |
| **资源工具**   | bash/file/pdf/web/calculator                                                                                                           | 执行正确；未实现的功能返回 not\_implemented，**不得伪造数据**                                                                                                                |

***

## 2. 生产前测试矩阵（怎么验证）

| 子系统  | 验证点                                                                           | 测试方法                           | 状态                                              |
| ---- | ----------------------------------------------------------------------------- | ------------------------------ | ----------------------------------------------- |
| 总线   | dispatch 有/无 handler、aspects 顺序与短路                                            | 单测                             | ✅ 基线通过                                          |
| 事件   | CorrelationId 因果链                                                             | 单测                             | ✅ 已覆盖（`Dispatch.parent_cid` 指向 `root_cid`，链路正常） |
| 内核   | AgentLoop 闭环                                                                  | demo\_engine.py                | ✅                                               |
| 内核   | 内核独立性（不依赖插件，bootstrap 可独立运行）                                                  | test\_kernel\_independence.py  | ✅ 已修复（移除 `_input_nodes()` 反向依赖）                 |
| 内核   | ResidentKernel reason 传递                                                      | 单测                             | ✅ 已覆盖（`bridge.py` 引用 `kernel.reason`，非死代码）      |
| 能力   | 渐进式披露                                                                         | 单测                             | ✅                                               |
| 能力   | mcp `_internal` 注入                                                            | 单测                             | ✅ 已修复（data=None 不再崩溃）                           |
| 能力   | memory importance 语义                                                          | 单测                             | ✅ 已修复（0 保留）                                     |
| 能力   | session 收敛会话数据本体（历史+状态），SessionBackend 解耦存储                                   | test\_session.py               | ✅ 新增（读/写/解耦/自定义后端/异常可观测）                        |
| 横切面  | permission 拦截                                                                 | 单测                             | ✅                                               |
| 横切面  | loop\_governor error 判定                                                       | 单测                             | ✅ 已修复（真实 result 判定）                             |
| 横切面  | trace\_recorder elapsed                                                       | 单测                             | ✅ 已修复（真实耗时）                                     |
| 通道   | UI/IM/CLI 闭环                                                                  | test\_channels.py              | ✅                                               |
| 全链路  | 端到端任务（入站→内核→闭环→披露→执行→横切面→出站）                                                  | test\_e2e.py                   | ✅ 新增                                            |
| 组合   | sandbox + tools/bash（沙箱接管 bash 执行）                                            | test\_combos.py                | ✅ 已修复（参数契约不一致）                                  |
| 组合   | circuit\_breaker + tools（熔断）                                                  | test\_combos.py                | ✅ 新增                                            |
| 组合   | budget\_guard + AgentLoop（预算熔断）                                               | test\_combos.py                | ✅ 新增                                            |
| 组合   | permission + skill exec（权限拦截 skill）                                           | test\_combos.py                | ✅ 新增                                            |
| 组合   | sandbox + skill exec（沙箱隔离 skill 脚本）                                           | test\_combos.py                | ✅ 新增                                            |
| 组合   | memory + AgentLoop（记忆检索注入决策输入）                                                | test\_combos.py                | ✅ 新增                                            |
| 组合   | knowledge + AgentLoop（能力门面渐进披露）                                               | test\_combos.py                | ✅ 新增                                            |
| 组合   | budget\_guard + loop\_governor（护栏叠加）                                          | test\_combos.py                | ✅ 新增                                            |
| 组合   | permission + sandbox（护栏叠加）                                                    | test\_combos.py                | ✅ 新增                                            |
| 全横切面 | 内核 + 全部 10 横切面：拦截有效（5 类护栏）                                                    | test\_all\_aspects.py          | ✅ 新增                                            |
| 全横切面 | 审计完整（append-only + SHA-256 hash 链，无遗漏）                                        | test\_all\_aspects.py          | ✅ 新增                                            |
| 全横切面 | 可追溯流转（tracing 每步调度 + telemetry 聚合）                                            | test\_all\_aspects.py          | ✅ 新增                                            |
| 全横切面 | 无遗漏（每个 dispatch 被所有观察类横切面记录）                                                  | test\_all\_aspects.py          | ✅ 新增                                            |
| 架构   | 内核=纯调度器（不决策/不重试/不超时）                                                          | test\_architecture.py          | ✅ 新增                                            |
| 架构   | 超时/重试可做成横切面（before 接管执行）                                                      | test\_architecture.py          | ✅ 新增                                            |
| 架构   | 依赖编排由 AgentLoop observe 天然处理                                                  | test\_architecture.py          | ✅ 新增                                            |
| 横切面  | timeout 全局超时（任务超时强制结束）                                                        | test\_timeout\_retry.py        | ✅ 新增                                            |
| 横切面  | retry 失败重试（before 接管 + 指数退避）                                                  | test\_retry.py                 | ✅ 新增                                            |
| 横切面  | wait\_timeout 等待超时（横切面治理，内核无硬编码）                                              | test\_wait\_timeout\_aspect.py | ✅ 新增（与 budget\_guard 同构）                        |
| 总线   | 健壮性：aspect 异常 / 订阅者异常仍走 after（运行不漏）                                           | test\_bus\_robustness.py       | ✅ 新增（#20/#21）                                   |
| 横切面  | retry 对 handler 抛异常也重试（生产工具失败通常抛异常）                                           | test\_retry.py                 | ✅ 新增（#22）                                       |
| 并发   | 治理类横切面按会话隔离（并发任务互不污染）                                                         | test\_concurrency.py           | ✅ 新增（#23）                                       |
| 内核   | wait 轮询间隔可配置（内核不读配置，只接受参数）                                                    | test\_kernel\_independence.py  | ✅ 新增（#24）                                       |
| 聊天   | 单轮对话：prompt 注入 system 指令、context 注入目标、telemetry 聚合输入节点                        | test\_chat.py                  | ✅ 新增                                            |
| 聊天   | 多轮对话：历史持久化 → 回灌 → 决策者看到前文（user+assistant）                                     | test\_chat.py                  | ✅ 新增                                            |
| 聊天   | 多轮对话：context 裁剪历史到最近 N 条（trim 语义）                                             | test\_chat.py                  | ✅ 新增                                            |
| 聊天   | 四场景链路：无插件降级 / 仅 prompt(system) / 仅 context(多轮) / prompt+context(system+user)  | test\_chat\_flow\.py           | ✅ 新增                                            |
| 聊天   | 日志/审计对得上：读操作不记审计，telemetry 记录输入节点与 input\_missing 广播                          | test\_chat\_flow\.py           | ✅ 新增                                            |
| 聊天   | goal 规范化：dict 包裹提取真实文本，不出现 {'goal':...} 嵌套冗余（符合行业命名）                          | test\_chat\_flow\.py           | ✅ 新增（#25）                                       |
| 聊天   | context 存储异常可观测：降级不阻塞 + 广播 context.store\_error（不静默吞错）                        | test\_chat\_flow\.py           | ✅ 新增（#26）                                       |
| 存储   | metadata 持久化                                                                  | 单测                             | ✅ 已修复（补 metadata 列）                             |
| 存储   | 存储契约化：SessionStore/StateStore/FileStore/MemoryBackend 四域均有 ABC 契约，实现可替换（直接注入） | test\_storage\_contracts.py    | ✅ 新增（7 用例：契约子类/方法签名/路径穿越防护/后端契约）                |
| 配置   | agents.yaml 解析                                                                | test\_agents.py                | ✅ 已修复（coder 能力集归一化）                             |
| 资源   | pdf 未实现语义                                                                     | 单测                             | ✅ 已修复（返回 not\_implemented）                      |

**验证命令（唯一真相源）**：

```bash
# 运行环境：项目虚拟环境 .venv（Python 3.13，基于 chato conda 环境创建）
# 依赖：pytest / pyyaml / httpx（已装入 .venv）
# 注意：系统 `python` 是 Microsoft Store 占位符，必须用 .venv 的解释器
.venv\Scripts\python.exe -m pytest tests -q        # 全量自动化测试（当前基线 152 passed）
.venv\Scripts\python.exe demos/demo.py             # 内核闭环 demo
.venv\Scripts\python.exe demos/demo_engine.py      # skill 门面闭环 demo
.venv\Scripts\python.exe demos/demo_channel_loop.py # 统一通道闭环 demo
```

***

## 3. 问题清单（已实证，含修复建议）

| #  | 严重度    | 位置                                                     | 问题                                                                                                                        | 修复建议                                                                                                                                             | 状态                                                                  |
| -- | ------ | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------- |
| 1  | **致命** | `agents.py`                                            | `agents.yaml` coder 的 `capabilities: {allow:[...]}` 是 dict，代码期望 list → coder 能力集为空                                        | 新增 `_normalize_capabilities()` 归一化为 list                                                                                                         | ✅ 已修复                                                               |
| 2  | 高      | `mcp.py`                                               | `result.data["_internal"]` 在 data=None 时 TypeError                                                                        | 仅当 data 为 dict 时注入                                                                                                                               | ✅ 已修复                                                               |
| 3  | 高      | `sqlite_memory_store.py`                               | importance=0 被改写为 0.5                                                                                                     | 仅 None 时用默认 0.5，0 保留                                                                                                                             | ✅ 已修复                                                               |
| 4  | 中      | `trace_recorder.py`                                    | elapsed\_ms 恒 0.0                                                                                                         | 新增 `loop.result` 广播，结算真实耗时                                                                                                                       | ✅ 已修复                                                               |
| 5  | 中      | `loop_governor.py`                                     | error 恒 False，无错误规则未实现                                                                                                    | 在 `loop.result` 用真实 result 判定 error                                                                                                              | ✅ 已修复                                                               |
| 6  | 中      | `pdf.py`                                               | 伪造 pages:300/chunks:42                                                                                                    | 未实现时返回 not\_implemented                                                                                                                          | ✅ 已修复                                                               |
| 7  | 中      | `sqlite_store.py`                                      | metadata 持久化丢失                                                                                                            | 表加 metadata 列并接入读写                                                                                                                               | ✅ 已修复                                                               |
| 8  | 低      | `resident.py`                                          | 构造 reason 是死代码                                                                                                            | 删除或接入 `_execute`                                                                                                                                 | ⚠️ 误判，已回滚（`bridge.py` 引用 `kernel.reason`，非死代码）                      |
| 9  | 中      | `loader.py`                                            | `_load_module` 用 `spec_from_file_location` 未设 `__package__` → `knowledge.py` 相对导入失败（demo.py 第 5 节报错）                      | 动态加载时用真实包路径作模块名（`_package_for` 推导）                                                                                                               | ✅ 已修复                                                               |
| 10 | 中      | `kernel/__init__.py`                                   | `Kernel._input_nodes()` 反向依赖插件层（`from ..plugins.loader import CapabilityLoader`），破坏内核独立性                                  | 移除 `_input_nodes()`，`bootstrap` 接受显式 `input_nodes`，装配层负责读配置                                                                                      | ✅ 已修复                                                               |
| 11 | 低      | `loader.py`                                            | `CapabilityLoader` 与 `AspectLoader` 的 `_load_module`/`_config_digest` 两份重复实现                                              | 提取为模块级函数 `_load_module`/`_config_digest`，两个 loader 共用                                                                                            | ✅ 已修复                                                               |
| 12 | 中      | `sandbox.py`                                           | sandbox 接管 bash 时读 `payload["command"]`，但 tools 门面把命令嵌套在 `payload["args"]["command"]` → 参数契约不一致，沙箱 bash 报"缺少必填参数 command" | `before` 从 `payload["args"]` 提取命令再交给 `run_bash`                                                                                                  | ✅ 已修复                                                               |
| 13 | 高      | `bus.py`                                               | `dispatch` 调用 handler 无 try/except，handler 抛异常时 after 不执行 → 审计/遥测缺失该次执行，违背"系统运行不漏"                                        | handler 调用包 try/except，异常时仍走 after（记失败）再 re-raise                                                                                                | ✅ 已修复                                                               |
| 14 | 低      | `bus.py`                                               | `publish` 被 before 阻断时直接 return，after 不执行 → telemetry 的 `_start` 残留 + 阻断广播无指标                                             | 阻断时也走 after，与 dispatch 一致，清理残留                                                                                                                   | ✅ 已修复                                                               |
| 15 | 中      | `audit.py`                                             | `_seq`/`_prev_hash` 是实例内存态，热更新重建横切管时新实例从全零起链 → hash 链断裂，防篡改保证失效                                                           | 初始化时从文件尾部续链（恢复 seq + prev\_hash）                                                                                                                 | ✅ 已修复                                                               |
| 16 | 低      | `loop.py`                                              | wait 超时（50 次）后直接结束任务，不重新推理 → 若能力未上线，任务因一个工具不可用而整体失败，而非换工具继续                                                               | 超时后注入"能力不可用"信号 → 重新推理选替代工具 → 仍 wait 才结束                                                                                                          | ✅ 已修复（迁移为 `wait_timeout.py` 横切面，与 budget\_guard 同构；内核移除硬编码 wait 计数） |
| 17 | 中      | `timeout.py`（新增）                                       | 生产就绪缺口：无全局超时，任务可能无限运行（仅靠 budget\_guard 轮数兜底，无时间维度）                                                                        | 新增 TimeoutAspect（监听 loop.iteration，超时强制结束）                                                                                                       | ✅ 已修复                                                               |
| 18 | 中      | `retry.py`（新增）                                         | 生产就绪缺口：无失败重试，暂时性失败（网络抖动/超时）直接放弃                                                                                           | 新增 RetryAspect（before 接管执行 + 指数退避重试，默认关闭）                                                                                                        | ✅ 已修复                                                               |
| 19 | 低      | `bus.py`                                               | 架构发现：重试**不能**放 after（返回值被丢弃）或门面层（不统一），应放 before 接管执行（与 sandbox 同模式）                                                       | 新增 `bus.invoke()` 供接管型横切面复用，避免递归                                                                                                                 | ✅ 已落地                                                               |
| 20 | 高      | `bus.py`                                               | aspect 自身 before 抛异常时 `_run_aspects` 直接 raise → after 阶段（审计/遥测）缺失，违背"运行不漏"（与 #13 handler 异常护栏不一致）                         | `_run_aspects` 隔离 aspect 故障（跳过继续），before 记首异常，dispatch/publish 走完 after 再 re-raise                                                               | ✅ 已修复                                                               |
| 21 | 中      | `bus.py`                                               | `publish` 订阅者 handler 抛异常时中断后续订阅者 + 跳过 after → 遥测缺失该广播，后续订阅者被拖垮                                                           | 订阅者逐个 try/except（记 ledger），不中断后续，走完 after 再 re-raise 首异常                                                                                         | ✅ 已修复                                                               |
| 22 | 高      | `retry.py`                                             | RetryAspect 用 `bus.invoke` 直接调 handler，handler 抛异常时 invoke 直接向上抛 → 重试逻辑不生效（生产工具失败通常抛异常）                                   | before 内 try/except 捕获 handler 异常视为一次失败，纳入重试；耗尽归一为失败 CapabilityResult                                                                            | ✅ 已修复                                                               |
| 23 | 高      | `budget_guard.py`/`loop_governor.py`/`wait_timeout.py` | 治理类横切面的跨轮次状态（`used`/`_history`/`waits`）是**全局单例**，并发任务（ResidentKernel Semaphore）互相污染：任务 A 的迭代数算到任务 B 头上，导致 B 被提前熔断         | 跨轮次状态按会话（`ctx.root_cid`）隔离为 dict，并发任务各自独立计数                                                                                                      | ✅ 已修复（test\_concurrency.py 验证）                                      |
| 24 | 低      | `loop.py`/`loader.py`/`api.py`                         | wait 轮询间隔 `0.02` 硬编码在内核，违背"内核=纯调度器、配置驱动"                                                                                  | 下沉为 `AgentLoop(wait_interval=)` 构造参数，装配层从 capabilities.yaml 读取，内核不读配置只接受参数+默认值兜底                                                                 | ✅ 已修复（test\_kernel\_independence.py 验证）                             |
| 25 | 中      | `context.py`                                           | `_default_collector` 把 goal dict 原样拼进文本 → `当前任务目标：{'goal': ...}` 嵌套冗余，不符合行业命名（goal 应为纯文本指令）                               | 新增 `_normalize_goal()`：dict 提取 `goal["goal"]`，字符串原样，其他 str() 兜底，拼出干净文本                                                                           | ✅ 已修复（test\_chat\_flow\.py 验证）                                      |
| 26 | 中      | `context.py`                                           | `_load_history` 存储异常 `except Exception: pass` 静默吞错，无日志/遥测 → 存储故障不可观测，违背"系统运行不漏"                                           | 记日志 + 广播 `context.store_error`（降级不阻塞，故障可观测）；handle/\_load\_history 改异步以 await publish                                                            | ✅ 已修复（test\_chat\_flow\.py 验证）                                      |
| 27 | 中      | `context.py`/`session.py`（新增）                          | 会话数据本体（历史+状态）散落：读在 context 插件、写在外部装配层，无独立归属 → 会话无法插件化、context 与存储耦合                                                       | 新增 `session` 插件收敛会话数据本体，`SessionBackend` 接口解耦存储（读 get\_history / 写 append\_message / save\_session）；context 改为从 session\_backend 取历史，不再直接碰 store | ✅ 已修复（test\_session.py 验证）                                          |

> 修复顺序：先致命（#1）→ 高（#2/#3）→ 中（#4/#5/#6/#7）→ 低（#8）。每个修复必须配套回归测试。
> 当前基线：`.venv\Scripts\python.exe -m pytest tests -q` → **152 passed**（原 37 + 新增 115）。

***

## 4. 强约束的软件工程开发体系

### 4.1 先读后改

- 任何改动前，先阅读目标文件及其周边上下文（imports、调用方、测试），理解现有模式。

- 遵循现有代码风格：命名、目录结构、库选择、类型标注。**不引入未确认存在的库**。

### 4.2 改动最小化

- 只做被要求的事，不多做、不少做。不主动创建文档（\*.md）或 README，除非明确要求。

- 优先编辑现有文件，不新建文件。

### 4.3 每个改动必须配套测试

- 每个功能改动必须配套测试（新增或修改 `tests/` 下的用例），**先写失败用例再修复**（TDD）。

- 改动完成后必须跑全量测试，**全绿才算完成**。

- 若改动涉及 lint/typecheck，跑对应命令（`eslint` 用于 `web/`，Python 侧以 pytest 为准）。

### 4.4 提交纪律

- **不主动 commit**。只有开发者明确要求时才提交。

- 提交前必须：全量测试通过 + lint/typecheck 通过。

### 4.5 修复流程（针对问题清单）

1. 定位根因（读代码 + 复现用例）；
2. 写失败测试用例，确认能复现；
3. 最小化修复；
4. 跑全量测试，全绿；
5. 更新问题清单状态（已修复/待修复）。

***

## 5. 安全与边界

- 危险命令会被 `permission`/`sandbox` 护栏拦截——这是**预期行为**，主 Agent 不应绕过护栏。

- 不记录、不打印任何密钥/敏感信息。

- 能力域由 `agents.yaml` 的 `capabilities.allow` 白名单限定，不越权扩大范围，除非开发者明确要求。

***

## 6. 工作流启动清单（主 Agent 每次开发前）

1. 读本文件，确认约束生效；
2. 确认 ollama 可用（`ollama list`），否则降级为直接执行；
3. 跑一次基线测试（`.venv\Scripts\python.exe -m pytest tests -q`），记录当前状态；
4. 对照问题清单，确认当前待修复项；
5. 发布任务给 AgentOS → 逐组件核对 → 记录问题 → 强约束修复 → 回归 → 验收。

