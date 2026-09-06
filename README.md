# libcore —— 事件网格（Mesh）工程的最小参考实现

> **libcore 是「Mesh（事件网格）工程」范式的一个最小可运行参考实现**：事件驱动 + 插件化的极薄内核。
> 内核只解决"通讯 + 决策 + 闭环"，业务逻辑全部以插件（能力 / 横切面）接入。

---

## 项目简介

libcore 以**事件总线为网、以运行时决策为边**，让 Graph 与 Loop 在同一基底上共存并可互相折叠。核心设计：

- **内核极薄**：`emit + listen` 只负责"通讯"，"决策"交给挂在网上的决策者（Agent）。
- **组件可替换**：能力、横切面、后端以 SPI 接入，可独立热更，不动内核。
- **自迭代成自然**：全量轨迹都在总线上，回灌即训练数据。

> 范式定名与定性（为什么需要 Mesh、三条独有特征、与 Graph/Loop 对比）见 [docs/mesh-engineering.md](docs/mesh-engineering.md)。
> 内核设计（事件总线 + Agent 调度环 + 装配）见 [docs/libcore-kernel-design.md](docs/libcore-kernel-design.md)。
> 内置插件清单（11 能力 + 10 横切面）见 [docs/libcore-capabilities.md](docs/libcore-capabilities.md) 与 [docs/libcore-aspects.md](docs/libcore-aspects.md)。

---

## 快速上手

### 普通路径（开箱即用）

开发者只需"选模型 + 注册能力 + 跑"，完全不碰适配器 / 装配：

```python
from libcore.api import Agent

agent = Agent(backend="ollama", model="qwen3:0.6b")

@agent.capability("cap.now", description="返回当前时间")
def now(payload):
    import datetime
    return {"now": datetime.datetime.now().isoformat(timespec="seconds")}

result = agent.run("获取当前时间")   # 剩下框架跑
```

### 高级路径（自定义决策 / 后端）

想自定义 `ReasonProvider.decide`，或用任意库写 backend（官方 / langchain / 自研），见 [docs/libcore-kernel-design.md](docs/libcore-kernel-design.md) 与 `libcore/agent/` 源码。

---

## 运行与验证

```bash
python demos/demo.py                 # 内核：点名/清单/横切面/护栏/热更新/插件自发现
python demos/demo_engine.py          # Skills 入口：find/read/resource/exec + 护栏收口
python demos/demo_llm_ollama.py       # 用你真机 ollama 验证适配器通用性
python demos/demo_migrate_file.py     # 真实能力迁移试点（旧 FileReadTool → 总线能力）
python demos/demo_resident.py         # 常驻协作内核：7×24 待机 + 事件驱动唤醒 + 优雅关闭
python demos/demo_agent_ui.py         # UI 第三层：agent 决策触发前端渲染（单端口同源）
python -m libcore.ui_bridge.server    # UI 桥接服务：单端口 HTTP + WebSocket + 静态
python -m pytest tests -q            # 自动化测试
```

---

## 目录结构

```
├── libcore/               # libcore 内核（Python）
│   ├── api.py             #   高层开箱即用 Agent（正式入口）
│   ├── kernel.py          #   便捷装配（组合根 bootstrap）
│   ├── config/            #   配置文件唯一真相源（capabilities.yaml / aspects.yaml）
│   ├── core/              #   内核（事件总线 / 信号原语 / 会话作用域）
│   ├── agent/             #   决策与闭环（ReasonProvider / 调度环 / 常驻内核 / LLM 后端）
│   ├── plugins/           #   插件子系统（能力 + 横切面 + 统一加载器）
│   ├── skills/            #   技能库（SKILL.md + 可选附件）
│   ├── tools/             #   工具定义库（TOOLS 注册表）
│   ├── mcp_servers/       #   MCP 工具定义库（TOOLS 注册表）
│   ├── knowledge/         #   知识库（加载器 / 切分器 / 嵌入 / 文档）
│   ├── storage/           #   存储（契约 / 文件 / SQLite / 状态）
│   ├── engine/            #   引擎族（传输引擎等，可替换底层）
│   ├── ui_bridge/         #   UI 桥接（单端口同源 + agent 集成）
│   └── demos/             #   演示脚本
├── docs/                  # 技术文档（范式 / 设计 / 演进 / 待办）
├── src/                   # 前端（Vue 3）
└── package.json           # npm 项目配置
```

---

## 文档

- [docs/README.md](docs/README.md) —— 文档中心
- [docs/mesh-engineering.md](docs/mesh-engineering.md) —— Mesh 工程范式论文
- [docs/libcore-kernel-design.md](docs/libcore-kernel-design.md) —— 内核设计
- [docs/libcore-capabilities.md](docs/libcore-capabilities.md) —— 能力插件体系
- [docs/libcore-aspects.md](docs/libcore-aspects.md) —— 横切面插件体系
- [docs/libcore-evolution-log.md](docs/libcore-evolution-log.md) —— 演进日志
- [docs/libcore-ui-layer2-bridge.md](docs/libcore-ui-layer2-bridge.md) —— UI 第二层：前后端桥接
- [docs/libcore-ui-layer3-agent.md](docs/libcore-ui-layer3-agent.md) —— UI 第三层：agent 集成
- [docs/libcore-migration-backlog.md](docs/libcore-migration-backlog.md) —— 技术债与迁移待办

---

## 状态

- **Milestone**：M0 — 最小可运行内核原型（受自动化测试保护）。
- **已验证理念**：Agent 唯一决策、点名直投（消灭隐性流程）、横切面为信号插件、护栏为内核一部分、全量轨迹为自迭代数据源、配置唯一真相源 + 文件驱动热更新、双层 API、真实旧能力可迁移、UI 插件闭环（agent 决策 → 渲染 → 事件回传）。
- **当前边界**：仍是骨架——决策策略默认"工具调用即点名"，尚无"经验库/自迭代回灌"实装；能力以演示级为主；无持久化与真实外部 API 入口。技术债与迁移待办见 [docs/libcore-migration-backlog.md](docs/libcore-migration-backlog.md)。

---

## 许可证

MIT License
