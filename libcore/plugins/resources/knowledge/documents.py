"""知识库文档模型（原生重写，不 import 旧 app.runtime）。

对齐旧 ``KBDocument``：text + metadata + score（归一化相似度 [0,1]）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class KBDocument:
    """知识库文档/文本块。

    score 语义：归一化相似度 [0,1]，越高越相似。
      - 写入向量库时：score = None
      - 从 search() 返回时：由后端填充归一化相似度
    """
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: Optional[float] = None
