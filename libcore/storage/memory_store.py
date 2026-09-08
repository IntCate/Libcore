"""记忆存储契约（抽象接口，不绑定任何存储实现）。

对齐"组件只依赖契约、不依赖实现"原则：memory 插件依赖本契约，不关心底层是
内存 / SQLite / 文件 / 向量库。开发者想换存储，就自己实现本接口 + 转换逻辑。

契约只定义"存什么、取什么"（数据形状），不定义"怎么存"。
"""
from __future__ import annotations

import re
from typing import List


def _keywords(query: str) -> List[str]:
    """把查询拆成关键词：英文按词，中文按二元切分（对齐旧 memory_episodic_fts）。"""
    q = (query or "").lower()
    kws = re.findall(r"[a-z0-9]+", q)
    for chunk in re.findall(r"[\u4e00-\u9fff]+", q):
        if len(chunk) == 1:
            kws.append(chunk)
        else:
            kws.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return [w for w in kws if w]


class MemoryBackend:
    """记忆后端接口：``retrieve(query, session_id, limit) -> list[str]``（已排序命中文本）。

    开发者可用自定义后端（如包装旧 MemoryRetrieverHybrid / SQLite / 向量库），
    替换默认内存实现。检索异常由上层捕获降级为空记忆。
    """
    def retrieve(self, query: str, *, session_id: str, limit: int = 5) -> List[str]:
        raise NotImplementedError
