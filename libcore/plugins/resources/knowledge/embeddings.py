"""嵌入提供方（原生重写，不 import 旧 app.runtime）。

对齐旧 ``IEmbeddingProvider``（TD-5 去 langchain，httpx 直连）：
- ``IEmbeddingProvider`` 协议：model_name / dimension / embed_query / embed_documents；
- ``OllamaEmbeddingProvider``：httpx 直连 POST {base}/api/embed；
- ``OpenAIEmbeddingProvider``：httpx 直连 POST {base}/v1/embeddings（兼容 OpenAI/DeepSeek 等）。

可选依赖 httpx 缺失时，构造抛 ImportError（由调用方捕获降级）。
"""
from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable


@runtime_checkable
class IEmbeddingProvider(Protocol):
    """嵌入提供方协议（零 langchain，httpx 直连或本地推理）。"""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_query(self, query: str) -> List[float]: ...

    def embed_documents(self, documents: List[str]) -> List[List[float]]: ...


class OllamaEmbeddingProvider:
    """Ollama 嵌入模型（httpx 直连 POST {base}/api/embed）。"""

    def __init__(self, model_name: str, base_url: Optional[str] = None,
                 timeout_s: float = 60.0) -> None:
        try:
            import httpx
        except ImportError as e:
            raise ImportError("OllamaEmbeddingProvider 需要 httpx") from e
        self._httpx = httpx
        self._model_name = model_name
        self._base_url = (base_url or "http://localhost:11434").rstrip("/")
        self._timeout_s = timeout_s
        self._dimension: Optional[int] = None
        self._client: Optional[httpx.Client] = None
        self._dimension = len(self.embed_query("测试文本"))

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension or 0

    def embed_query(self, query: str) -> List[float]:
        return self._embed([query])[0]

    def embed_documents(self, documents: List[str]) -> List[List[float]]:
        return self._embed(documents)

    def _embed(self, inputs: List[str]) -> List[List[float]]:
        client = self._get_client()
        resp = client.post(
            f"{self._base_url}/api/embed",
            headers={"Content-Type": "application/json"},
            json={"model": self._model_name, "input": inputs},
            timeout=self._timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return [list(e) for e in data.get("embeddings", [])]

    def _get_client(self):
        if self._client is None:
            self._client = self._httpx.Client()
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


class OpenAIEmbeddingProvider:
    """OpenAI 兼容嵌入模型（httpx 直连 POST {base}/v1/embeddings）。"""

    def __init__(self, model_name: str, api_key: Optional[str] = None,
                 api_base_url: Optional[str] = None, timeout_s: float = 30.0) -> None:
        try:
            import httpx
        except ImportError as e:
            raise ImportError("OpenAIEmbeddingProvider 需要 httpx") from e
        self._httpx = httpx
        self._model_name = model_name
        self._api_key = api_key
        self._api_base_url = (api_base_url or "https://api.openai.com").rstrip("/")
        self._timeout_s = timeout_s
        self._dimension: Optional[int] = None
        self._client: Optional[httpx.Client] = None
        self._dimension = len(self.embed_query("测试文本"))

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension or 0

    def embed_query(self, query: str) -> List[float]:
        return self._embed([query])[0]

    def embed_documents(self, documents: List[str]) -> List[List[float]]:
        return self._embed(documents)

    def _embed(self, inputs: List[str]) -> List[List[float]]:
        client = self._get_client()
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        resp = client.post(
            f"{self._api_base_url}/v1/embeddings",
            headers=headers,
            json={"model": self._model_name, "input": inputs},
            timeout=self._timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        items = sorted(data.get("data", []), key=lambda it: it.get("index", 0))
        return [list(it["embedding"]) for it in items]

    def _get_client(self):
        if self._client is None:
            self._client = self._httpx.Client()
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
