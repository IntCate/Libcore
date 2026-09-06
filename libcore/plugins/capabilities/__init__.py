"""libcore.plugins.capabilities —— 能力插件（可被点名的"节点"）。

与横切面插件（aspects/）不同，能力插件是 Agent 可点名的一个"目标"，
Agent 通过 ``target`` 调用它、拿结果。它在总线上进"点名表"（_handlers）。

加载机制：能力插件走**自发现加载器**（``plugins/loader.CapabilityLoader``），
配置 = 唯一真相源，支持文件驱动热更新。用法：

1. 在 ``capabilities/`` 放一个 ``*.py``（非 _ 开头）；
2. 模块暴露 ``register(bus)``，在其中 ``bus.on(target, handler, meta=...)``；
3. ``loader.load(config)`` 后，Agent 下一轮即从可呼叫清单发现并调用它。

本目录之以"自发现 + 热更新"为主，是因为能力是无序的目标集合，
谁被点名才执行，顺序无关紧要——适合激进自动登记。
"""