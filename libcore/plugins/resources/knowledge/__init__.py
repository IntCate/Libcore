"""知识库纯库：文档模型 + 加载器 + 切分器 + 嵌入提供方。

对齐旧 ``app.runtime.kernel.knowledge_components`` 与 ``embeddings``，
但按 libcore 收敛为纯库模块（非总线节点），供 knowledge / context 注入使用。
"""
from .documents import KBDocument
from .loaders import DocumentLoader, load_document
from .splitter import RecursiveSplitter
from .embeddings import (
    IEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
)

__all__ = [
    "KBDocument",
    "DocumentLoader",
    "load_document",
    "RecursiveSplitter",
    "IEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "OpenAIEmbeddingProvider",
]
