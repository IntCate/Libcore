"""前端组件清单存储：前端上报 / 后端查询的轻量内存后端。

第二层阶段：内存存储即可（agentOS 持久化后续再考虑）。
后端 ui 能力节点经 GET 查询这份清单，向 agent 披露"前端现在有哪些组件可用"。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List


class ManifestStore:
    """前端 component 清单的内存存储。线程会话 + 写读加锁，容忍并发。

    数据结构：``{"components": ["chat", "table", "card", ...]}``。
    """

    def __init__(self) -> None:
        self._components: List[str] = []
        self._lock = threading.Lock()

    def update(self, components: List[str]) -> None:
        """整体替换组件清单（前端每次注册表变更全量上报）。"""
        with self._lock:
            self._components = list(components or [])

    def snapshot(self) -> Dict[str, Any]:
        """返回清单快照给后端查询。"""
        with self._lock:
            return {"components": list(self._components)}

    def has(self, component: str) -> bool:
        with self._lock:
            return component in self._components