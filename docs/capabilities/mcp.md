# MCP 工具库

> 从零开始理解 libcore 的 MCP 能力：如何用"代码定义"组织 MCP 工具，agent 如何渐进披露地调用。

## 1. 什么是 MCP

**MCP（Model Context Protocol）** 是模型上下文协议，用于让 agent 调用外部工具/服务。libcore 的 `mcp` 门面与 `tools` / `skill` 同为一级"真入口"。

MCP 工具定义放在 `libcore/plugins/resources/mcp_servers/`（与 `tools/` 平级的**非总线节点**定义库），被 `mcp` 门面启动时直接文件扫描聚合。

```
libcore/plugins/resources/mcp_servers/
├── calculator.py   # 暴露 TOOLS = {"calculator": {...}}
└── slack.py
```

## 2. MCP 工具定义格式

与 tools 相同的 `TOOLS` 纯文件注册表：

```python
# libcore/plugins/resources/mcp_servers/calculator.py
TOOLS = {
    "calculator": {
        "name": "calculator",
        "description": "执行数学计算",
        "ops": ["add", "sub", "mul", "div"],
        "schema": {"a": {"type": "number"}, "b": {"type": "number"}},
        "handler": _handle_calc,   # 接收 Dispatch，返回 CapabilityResult
    },
}
```

## 3. 渐进披露（省 token）

manifest 只暴露**一个** MCP 入口 `mcp`，**具体 MCP 工具的 schema 不常驻清单**——agent 需要时才逐层索取：

| 层级     | op         | 作用                                           |
| ------ | ---------- | -------------------------------------------- |
| **L1** | `mcp find` | 列 MCP 工具/服务索引（key + 名称 + 一句 description），供选择 |
| **L2** | `mcp read` | 取某 MCP 工具完整参数 schema（按需展开）                   |
| **L3** | `mcp run`  | 执行某 MCP 工具（payload 传 name + tool\_op + args） |

## 4. 调用示例

```python
# L1：列 MCP 工具索引
await bus.dispatch(Dispatch(target="mcp", op="find", payload={}))
# → {"tools": [{"key": "calculator", "name": "calculator", "ops": ["add"], "description": "..."}]}

# L2：读某 MCP 工具完整 schema
await bus.dispatch(Dispatch(target="mcp", op="read", payload={"name": "calculator"}))

# L3：执行某 MCP 工具
await bus.dispatch(Dispatch(target="mcp", op="run",
                            payload={"name": "calculator", "tool_op": "add", "args": {"a": 1, "b": 2}}))
```

## 5. 设计说明

- **真实部署**：`mcp.run` 应向**远端 MCP server** 透发（走 MCP 协议，client 实现归组件层 services，不 import 进内核顶部）。

- **首期**：`libcore/plugins/resources/mcp_servers/` 用本地代码定义承载，便于内核/护栏先行闭环验证。

- **诚实披露**：后端未接线的服务如实返回 `not_implemented`，绝不为凑数据编造。

- **内部细节暴露**：具体 MCP 工具 handler 是**开放组件，不走总线**（保持单一契约点 + 渐进披露）。但 `mcp run` 门面会把内部子调用细节塞进 `result.data["_internal"]`（`sub_target` / `sub_op` / `sub_args`），随结果上抛，使 tracing/logging 能还原到具体工具这一层，而不只是 `mcp run` 门面。

## 6. 安全设计

- **执行收敛**：执行信号收敛于 `mcp` 的 run op，aspect 护栏（sandbox/permission/circuit\_breaker/audit）只签此处。

- **op 校验**：`run` 时校验 op 是否在 `ops` 列表内，不在则拒绝。

## 7. 装配

```python
from libcore.plugins.capabilities import mcp
mcp.register(bus)   # 默认 MCP 工具定义库 libcore/plugins/resources/mcp_servers
```

## 8. 代码位置

- `libcore/plugins/capabilities/mcp.py`：MCP 门面 + McpEngine

- `libcore/plugins/resources/mcp_servers/`：MCP 工具定义库

