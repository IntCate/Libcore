# libcore 能力插件体系

能力（Capability）是 agent 可点名的可执行节点。agent 通过 dispatch 调用它。

能力分三类：门面（skill / tools / mcp / knowledge）、输入节点（context / prompt / memory）、系统能力（im / ui / cli / api / model）。

每个能力是一个 .py 文件，暴露 register(bus) 函数，在其中 bus.on(target, handler, meta=...) 登记进点名表。

libcore/config/capabilities.yaml 是能力的唯一真相源，改 enabled 保存即可热更新。
