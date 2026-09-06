"""im 平台适配器子包：每个平台一个适配器模块，实现 ``adapter(op, payload) -> dict`` 契约。

适配器不持有连接生命周期（不自己 connect/disconnect），每次调用按需建立短连接
（或复用进程内缓存的客户端），符合 agentOS"能力节点 = 原子动作"的定位。

各平台接入逻辑借鉴 hermes-agent（只借鉴 SDK 调用方式，不引入其网关级抽象）。
"""
