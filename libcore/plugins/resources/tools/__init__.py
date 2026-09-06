"""工具定义子包：每个模块暴露 ``TOOLS: dict[str, dict]``，由顶层 ``tools`` 门面聚合。

不写 ``register(bus)``（不是总线节点）；loader.scan() 用 ``glob("*.py")`` 非递归，
故本子包不会被当成独立能力加载。
"""