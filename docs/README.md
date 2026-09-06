# libcore 文档中心

> 本目录是 libcore 项目的全部技术文档，从零开始描述"这个系统是什么、怎么用"。

***

## 📂 目录结构

```
docs/
├── README.md                          ← 你在这里（文档总索引）
├── core/                              ← 核心机制
│   ├── libcore_agentos.md             ← agentOS 总览（从零理解 libcore）
│   ├── event-bus.md                   ← 事件总线与通讯机制（Dispatch/Notice/Aspect/Channel）
│   ├── kernel.md                      ← 内核（决策层：AgentLoop/ReasonProvider/ResidentKernel）
│   ├── session.md                     ← 会话存储（SessionStore 契约）
│   └── channels.md                    ← 统一通讯机制（Channel 抽象）
├── capabilities/                      ← 能力插件体系
│   ├── capabilities.md                ← 能力插件总览（注册/配置/热更新/渐进披露）
│   ├── aspects.md                     ← 横切面（Aspect 拦截）
│   ├── skill.md                       ← Skill 技能库
│   ├── tools.md                       ← Tools 工具库
│   ├── mcp.md                         ← MCP 工具库
│   ├── knowledge.md                   ← Knowledge 知识库
│   ├── prompt.md                      ← Prompt 提示词
│   ├── context.md                     ← Context 上下文
│   └── memory.md                      ← Memory 记忆
├── engine/                            ← 引擎与入口
│   ├── ui.md                          ← UI 界面（UiBridge + UiChannel）
│   ├── im.md                          ← IM 即时通讯（平台适配器 + ImChannel）
│   └── mesh-engineering.md            ← Mesh（事件网格）工程范式
└── archive/                           ← 归档的旧文档（重构/迁移角度，已废弃）
```

***

## 🚀 新人入口

| 顺序 | 文档                                                             | 读它做什么                           |
| -- | -------------------------------------------------------------- | ------------------------------- |
| 1  | [core/libcore\_agentos.md](./core/libcore_agentos.md)          | 从零理解 libcore：内核 + 能力 + 横切面 + 通道 |
| 2  | [core/event-bus.md](./core/event-bus.md)                       | 理解通讯核心：点名/广播/横切面/统一通道           |
| 3  | [core/kernel.md](./core/kernel.md)                             | 理解决策层：调度环/决策 SPI/常驻内核           |
| 4  | [capabilities/capabilities.md](./capabilities/capabilities.md) | 理解能力插件体系：注册/配置/热更新              |
| 5  | [capabilities/aspects.md](./capabilities/aspects.md)           | 理解横切面：日志/护栏/追踪                  |
| 6  | [engine/mesh-engineering.md](./engine/mesh-engineering.md)     | 理解 Mesh 范式（libcore 的范式论文）       |

***

## 📊 文档状态总览

| 文档                                                             | 状态   | 说明          |
| -------------------------------------------------------------- | ---- | ----------- |
| [core/libcore\_agentos.md](./core/libcore_agentos.md)          | ✅ 现行 | agentOS 总览  |
| [core/event-bus.md](./core/event-bus.md)                       | ✅ 现行 | 事件总线与通讯机制   |
| [core/kernel.md](./core/kernel.md)                             | ✅ 现行 | 内核（决策层）     |
| [core/session.md](./core/session.md)                           | ✅ 现行 | 会话存储        |
| [capabilities/capabilities.md](./capabilities/capabilities.md) | ✅ 现行 | 能力插件体系      |
| [capabilities/aspects.md](./capabilities/aspects.md)           | ✅ 现行 | 横切面         |
| [capabilities/skill.md](./capabilities/skill.md)               | ✅ 现行 | Skill 技能库   |
| [capabilities/tools.md](./capabilities/tools.md)               | ✅ 现行 | Tools 工具库   |
| [capabilities/mcp.md](./capabilities/mcp.md)                   | ✅ 现行 | MCP 工具库     |
| [capabilities/knowledge.md](./capabilities/knowledge.md)       | ✅ 现行 | Knowledge 知识库 |
| [capabilities/prompt.md](./capabilities/prompt.md)             | ✅ 现行 | Prompt 提示词  |
| [capabilities/context.md](./capabilities/context.md)           | ✅ 现行 | Context 上下文 |
| [capabilities/memory.md](./capabilities/memory.md)             | ✅ 现行 | Memory 记忆   |
| [engine/ui.md](./engine/ui.md)                                 | ✅ 现行 | UI 界面       |
| [engine/im.md](./engine/im.md)                                 | ✅ 现行 | IM 即时通讯     |
| [engine/mesh-engineering.md](./engine/mesh-engineering.md)     | ✅ 现行 | Mesh 工程范式   |

***

## 🔧 快速上手

### 验证内核已就绪

```bash
python demos/demo.py                 # 内核：点名/清单/横切面/护栏/热更新/插件自发现
python demos/demo_engine.py          # Skills 入口：find/read/resource/exec + 护栏收口
python demos/demo_llm_ollama.py       # 用你真机 ollama 验证适配器通用性
python demos/demo_channel_loop.py     # 统一通道闭环：UI/IM/CLI 走同一条链路
python demos/demo_agent_ui.py         # UI 第三层：agent 决策触发前端渲染（单端口同源）
python -m pytest tests -q            # 自动化测试
```

### 写第一个插件

1. 在 `libcore/plugins/capabilities/` 下创建能力插件（暴露 `register(bus)`）
2. 按 [capabilities/capabilities.md](./capabilities/capabilities.md) 的契约编写
3. 在 `libcore/config/capabilities.yaml` 里声明 `enabled: true`
4. 启动应用，`CapabilityLoader` 自动扫描注册（配置 = 唯一真相源 + 文件驱动热更新）

***

## 📐 文档规范

- **命名**：按主题分目录（core / capabilities / engine），文件名用 kebab-case 或 snake\_case

- **版本**：文档头部用 `> **状态**` 标注；已实施去掉 DRAFT，规划中保留

- **交叉引用**：用相对路径链接，如 `[内核](./core/kernel.md)`

- **从零角度**：文档描述"这个系统是什么、怎么用"，不写重构/迁移对照

<br />
