# 能力插件体系（Capabilities）

> 从零开始理解 libcore 的能力插件：agent 能点名调用什么、怎么注册、怎么配置、怎么热更新。

## 1. 什么是能力

**能力（Capability）** 是 agent 可点名的可执行节点。agent 通过 `dispatch(Dispatch(target=..., op=..., payload=...))` 调用它。

能力分两类：

| 类型 | 例子 | 说明 |
| --- | --- | --- |
| **门面（Facade）** | skill / tools / mcp / knowledge | 渐进披露的"真入口"，具体资源按需索取 |
| **输入节点（Input Node）** | context / prompt / memory | 每轮决策前被内核点名，产出上下文 |
| **系统能力（System）** | im / ui / cli / api / model | agent 接管系统后调度的能力 |

## 2. 能力如何注册

每个能力是一个 `.py` 文件，暴露 `register(bus)` 函数：

```python
# libcore/plugins/capabilities/my_cap.py
from libcore.kernel.bus import Dispatch, CapabilityResult

DESCRIPTION = "我的能力：做某件事"

def register(bus) -> None:
    def handle(d: Dispatch) -> CapabilityResult:
        # 处理 op 分发
        return CapabilityResult(ok=True, data={...})
    bus.on("my_cap", handle, meta={"description": DESCRIPTION, "ops": ["run"]})
```

`bus.on(target, handler, meta=...)` 把能力登记进点名表，`meta` 登记能力"被发现"所需的描述与 schema，随后经 `bus.manifest()` 暴露给 agent 作为可呼叫清单。

## 3. 配置驱动（唯一真相源）

`libcore/config/capabilities.yaml` 是能力的**唯一真相源**：

```yaml
plugins:
  - name: skill
    enabled: true
    description: 技能库入口...
  - name: tool
    enabled: true
    description: 工具库入口...
  - name: my_cap
    enabled: true
    description: 我的能力...
```

- **开关**：改 `enabled` 保存即可，`CapabilityLoader.watch()` 自动对总线 on/off。
- **描述/ops**：配置里的 description/ops 会覆盖插件模块里的 meta。
- **自发现**：`discover()` 扫目录，把新插件写回配置（保留已设的开关/描述）。

## 4. 加载器（CapabilityLoader）

`libcore/plugins/loader.py` 的 `CapabilityLoader` 负责把配置同步到总线：

```python
loader = CapabilityLoader(bus, "libcore/plugins/capabilities")
loader.load()          # 把总线同步到配置描述的状态（配置 = 唯一真相源）
loader.discover()      # 扫目录，把新插件写回配置
await loader.watch()   # 监听配置变化，变了就 reconcile 并广播 capability.changed
```

### 热更新

`watch()` 监听配置文件变化，变化时：
1. `reconcile()` 对总线做 on/off diff。
2. 广播 `Notice(topic="capability.changed")`。
3. Agent 下一轮自动刷新清单。

## 5. 渐进披露（省 token）

manifest 只暴露**入口**，具体资源的 schema **不常驻清单**，agent 需要时才逐层索取：

| 门面 | L1 索引 | L2 详情 | L3 执行 |
| --- | --- | --- | --- |
| **skill** | `find` 列技能目录 | `read` 取 SKILL.md | `resource` 读附件 / `exec` 执行脚本 |
| **tools** | `find` 列工具索引 | `read` 取完整 schema | `run` 执行 |
| **mcp** | `find` 列 MCP 工具索引 | `read` 取完整 schema | `run` 执行 |
| **knowledge** | `find` 列知识库索引 | `read` 取元信息 | `search` 召回 / `ingest` 入库 |

## 6. 决策输入节点

`input_nodes` 配置驱动内核每轮决策前点名哪些节点：

```yaml
input_nodes:
  - target: context
    slot: user
    trim: {last_n: 20, exclude_last_user_if_present: false}
  - target: prompt
    slot: system
  - target: memory
    slot: user
```

- `slot` 决定消息在最终 messages 中的位置（system 在前，user 在后）。
- 加一个输入节点只改这里，**内核零改动**。
- 无节点时内核降级为最简默认，不报错。

## 7. 内置能力一览

| 能力 | target | 类型 | 说明 |
| --- | --- | --- | --- |
| skill | `skill` | 门面 | 技能库（find/read/resource/exec） |
| tool | `tools` | 门面 | 工具库（find/read/run） |
| mcp | `mcp` | 门面 | MCP 工具库（find/read/run） |
| knowledge | `knowledge` | 门面 | 知识库（find/read/search/ingest/stats/clear） |
| context | `context` | 输入节点 | 本次会话上下文 |
| prompt | `prompt` | 输入节点 | 强化系统指令 |
| memory | `memory` | 输入节点 | 记忆强化 |
| api | `api` | 系统 | 数据获取 |
| ui | `ui` | 系统 | 渲染决策 |
| im | `im` | 系统 | IM 收发 |
| cli | `cli` | 系统 | 终端输出 |
| model | `model` | 系统 | 模型管理 |

## 8. 代码位置

- `libcore/plugins/loader.py`：CapabilityLoader / AspectLoader
- `libcore/plugins/capabilities/`：能力节点
- `libcore/config/capabilities.yaml`：能力配置唯一真相源
