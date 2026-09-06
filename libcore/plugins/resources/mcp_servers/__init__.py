"""MCP 工具定义子包：每个模块暴露 ``TOOLS: dict[str, dict]``，由顶层 ``mcp`` 门面聚合。

不写 ``register(bus)``（不是总线节点）；loader.scan() 用 ``glob("*.py")`` 非递归，
故本子包不会被当成独立能力加载。真实部署时这些定义可替换/增强为对远端 MCP
server 的工具发现与透发（client 归组件层，不走内核顶部 import）。
"""