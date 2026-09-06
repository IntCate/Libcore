# Tools 工具库

> 从零开始理解 libcore 的 Tools 能力：如何用"代码定义"组织工具，agent 如何渐进披露地调用。

## 1. 什么是 Tool

**Tool（工具）** 是**代码定义**的可执行单元。工具定义放在 `libcore/plugins/resources/tools/`（与 `skills/` 平级的**非总线节点**定义库），被 `tools` 门面启动时直接文件扫描聚合。

```
libcore/plugins/resources/tools/
├── bash.py      # 暴露 TOOLS = {"bash": {...}}
├── file.py
├── pdf.py
├── todo.py
└── web.py
```

## 2. 工具定义格式

每个工具模块暴露 `TOOLS` 纯文件注册表：

```python
# libcore/plugins/resources/tools/bash.py
TOOLS = {
    "bash": {
        "name": "bash",
        "description": "执行 shell 命令",
        "ops": ["run"],
        "schema": {"command": {"type": "string", "description": "要执行的命令"}},
        "handler": _handle_bash,   # 接收 Dispatch，返回 CapabilityResult
    },
}
```

- `key`：工具唯一标识（dict 的 key）

- `name`：工具名（缺省用 key）

- `description`：工具描述（find 时展示）

- `ops`：支持的操作列表

- `schema`：参数 schema（read 时展开）

- `handler`：执行函数，签名 `handler(Dispatch) -> CapabilityResult`

## 3. 渐进披露（省 token）

manifest 只暴露**一个**工具入口 `tools`，**具体工具的 schema 不常驻清单**——agent 需要时才逐层索取：

| 层级     | op           | 作用                                      |
| ------ | ------------ | --------------------------------------- |
| **L1** | `tools find` | 列工具索引（key + 名称 + 一句 description），供选择    |
| **L2** | `tools read` | 取某工具完整参数 schema（按需展开）                   |
| **L3** | `tools run`  | 执行某工具（payload 传 name + tool\_op + args） |

## 4. 调用示例

```python
# L1：列工具索引
await bus.dispatch(Dispatch(target="tools", op="find", payload={}))
# → {"tools": [{"key": "bash", "name": "bash", "ops": ["run"], "description": "..."}]}

# L2：读某工具完整 schema
await bus.dispatch(Dispatch(target="tools", op="read", payload={"name": "bash"}))
# → {"key": "bash", "schema": {"command": {...}}}

# L3：执行某工具
await bus.dispatch(Dispatch(target="tools", op="run",
                            payload={"name": "bash", "tool_op": "run", "args": {"command": "ls"}}))
```

## 5. 安全设计

- **执行收敛**：执行信号收敛于 `tools` 的 run op，aspect 护栏（sandbox/permission）只签此处。

- **op 校验**：`run` 时校验 op 是否在 `ops` 列表内，不在则拒绝。

## 6. 装配

```python
from libcore.plugins.capabilities import tool
tool.register(bus)   # 默认工具定义库 libcore/plugins/resources/tools
```

可注入自定义工具定义目录：

```python
tool.register(bus)   # ToolEngine(tools_root=...) 可注入
```

## 7. 代码位置

- `libcore/plugins/capabilities/tool.py`：Tools 门面 + ToolEngine

- `libcore/plugins/resources/tools/`：工具定义库

> 注意：门面插件文件名是 `tool.py`（避免与 `tools/` 子包同名），但总线暴露的 target 仍是 `tools`。

