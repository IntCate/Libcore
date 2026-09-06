"""传输引擎抽象基类（正式路径，由 transport_verify 归档演进而来）。

设计原则（对齐架构敲定结论）：
- 传输引擎独立，不写死在应用层 —— 为阶段二 agentOS 调配预留（调用方从应用层换成
  transport 能力节点，引擎零改动）。
- 接口与内核 Dispatch 对齐：方法名 = op，参数 = payload。
- 引擎管"怎么传"（端口/HTTP/WebSocket/静态文件），agent 管"调配策略"。

方法 —— 未来 transport 能力节点将按同名 op 转调：
    open(spec)   -> str  开一个通道（http/websocket），返回通道 id
    close(id)    -> None 关一个通道
    route(path, handler, method="POST") 注册 HTTP 路由
    push(channel, data)  主动推数据（WebSocket 下行）
    mount_static(dist_dir) 挂载前端文件，单端口同源
"""
from __future__ import annotations

from typing import Any, Callable, Dict

# HTTP 路由 handler 签名：async (request: dict) -> dict
# 返回的 dict 会被引擎翻译成 JSON 响应返回给浏览器。
Handler = Callable[[Dict[str, Any]], Dict[str, Any]]


class TransportEngine:
    """传输引擎抽象基类。具体实现（HTTP/WebSocket/静态）由子类提供。"""

    def open(self, spec: dict) -> str:
        raise NotImplementedError

    def close(self, id: str) -> None:
        raise NotImplementedError

    def route(self, path: str, handler: Handler, method: str = "POST") -> None:
        raise NotImplementedError

    def push(self, channel: str, data: dict) -> None:
        raise NotImplementedError

    def mount_static(self, dist_dir: str) -> None:
        raise NotImplementedError