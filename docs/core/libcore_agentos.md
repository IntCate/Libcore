# libcore agentOS 总览

> 从零开始理解 libcore：一个"内核 + 能力插件 + 横切面"的 agent 操作系统骨架。

## 1. 这是什么

libcore 是一个 **agent 操作系统（agentOS）骨架**。它不绑定任何具体业务，而是提供一套**让 agent 能持续运转、被外部入口（UI / IM / CLI）驱动、按需调用能力**的底层机制。

核心心智模型只有一句话：

> **总线负责"投递"，agent 负责"决策"，能力负责"执行"，横切面负责"护栏"。**

## 2. 四个角色

| 角色                 | 位置                              | 职责                                       | 类比   |
| ------------------ | ------------------------------- | ---------------------------------------- | ---- |
| **事件总线（EventBus）** | `libcore/kernel/bus/`           | 唯一的信号通道：点名投递 + 广播感知 + 横切面拦截              | 电话总机 |
| **Agent（决策层）**     | `libcore/kernel/agent/`         | 调度环：读输入 → 决策 → 调用能力 → 观察结果               | 话务员  |
| **能力（Capability）** | `libcore/plugins/capabilities/` | 被 agent 点名的可执行节点（skill/tools/mcp/im/ui…） | 分机   |
| **横切面（Aspect）**    | `libcore/plugins/aspects/`      | 对所有流经总线的信号统一拦截（日志/护栏/追踪）                 | 总机录音 |

## 3. 一次对话怎么流转

```
外部入口（UI / IM / CLI）
   │  每个入口 = 一个 Channel
   ▼
Channel.ingest(消息) ──广播──▶ EventBus.publish(Notice("channel.inbound"))
   │
   ▼
薄桥接 wire_inbound_to_kernel ──▶ ResidentKernel.submit(goal)
   │
   ▼
AgentLoop.run(session_id=channel_id)
   │  读输入节点（context/prompt）→ 决策 → dispatch 能力 → 观察
   ▼
agent dispatch("skill"/"tools"/"im"...)
   │
   ▼
能力节点执行 → Channel.reply 送回原会话
```

## 4. 目录结构

```
libcore/
├── api.py                  # 开箱即用高层 API（Agent 类）
├── kernel/                 # 内核：总线 + 决策层
│   ├── bus/                #   EventBus / Dispatch / Notice / Aspect
│   └── agent/              #   AgentLoop / ReasonProvider / ResidentKernel
├── channels/               # 统一通讯机制：Channel 抽象 + UI/IM/CLI 通道
├── plugins/                # 插件体系
│   ├── loader.py           #   能力加载器 + 横切面加载器（配置驱动）
│   ├── capabilities/       #   能力节点（skill/tools/mcp/im/ui/cli/...）
│   ├── aspects/            #   横切面（permission/sandbox/audit/...）
│   └── resources/          #   资源库（被各门面扫描）
│       ├── tools/          #     工具定义库（被 tools 门面扫描）
│       ├── skills/         #     技能库（SKILL.md + 附件，被 skill 门面扫描）
│       └── mcp_servers/    #     MCP 工具定义库（被 mcp 门面扫描）
├── llm/                    # LLM 适配器库（ollama/openai/anthropic/...）
├── storage/                # 存储纯库（session/memory/state/file）
├── engine/                 # 传输引擎（HTTP/WebSocket/静态）
├── ui_bridge/              # UI 桥接（前端 RPC → 总线）
└── config/                 # 配置唯一真相源（capabilities.yaml / aspects.yaml / llm.yaml）
```

## 5. 开箱即用

普通开发者不需要懂"适配器 / backend / 装配"，用 `libcore.api.Agent` 即可：

```python
from libcore.api import Agent

agent = Agent(backend="ollama", model="qwen3:0.6b")

@agent.capability("cap.now", description="返回当前时间")
def now(payload):
    import datetime
    return {"now": datetime.datetime.now().isoformat(timespec="seconds")}

result = agent.run("获取当前时间")
```

## 6. 三种运行模式

| 模式       | 入口                                  | 适用                      |
| -------- | ----------------------------------- | ----------------------- |
| **单次任务** | `agent.run(goal)`                   | 跑一个任务就结束                |
| **常驻内核** | `agent.serve()` + `kernel.submit()` | 7×24 待机，事件驱动唤醒          |
| **统一通讯** | `agent.serve_channels()`            | 接 UI/IM/CLI 通道，消息从哪来回哪去 |

## 7. 设计原则

1. **总线止步于投递**：总线不判断"下一步该干嘛"，只负责点名/广播/拦截。
2. **决策止步于 agent**：只有 agent 决定"调用哪个能力、带什么参数"。
3. **能力渐进披露**：manifest 只暴露入口，具体 skill/tool 的 schema 需要时才索取（省 token）。
4. **配置驱动**：`capabilities.yaml` / `aspects.yaml` 是唯一真相源，改配置即热更新。
5. **内核零改动**：加能力、加通道、加横切面都不碰 `kernel/`。

## 8. 文档导航

| 主题                  | 文档                                                                        |
| ------------------- | ------------------------------------------------------------------------- |
| 事件总线与通讯机制           | `core/event-bus.md`                                                       |
| 内核（决策层）             | `core/kernel.md`                                                          |
| 能力插件体系              | `capabilities/capabilities.md`                                            |
| 横切面                 | `capabilities/aspects.md`                                                 |
| 统一通讯机制（Channel）     | `core/channels.md`                                                        |
| Skill / Tools / MCP | `capabilities/skill.md` / `capabilities/tools.md` / `capabilities/mcp.md` |
| UI / IM             | `engine/ui.md` / `engine/im.md`                                           |
| 存储（session/memory）  | `core/session.md`                                                          |

