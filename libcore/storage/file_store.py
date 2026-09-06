"""本地文件存储（原生重写，不 import 旧 app.runtime）。

对齐旧 ``LocalFileStore`` 语义：
- write(rel_path, data) → key（规范化相对路径）；read/delete/stat 按 key 操作；
- 路径穿越防护：realpath 必须落在 base_dir 内（避免 ../../ 越权读写）。
"""
from __future__ import annotations

import mimetypes
import os
from typing import Any, Dict


class LocalFileStore:
    """本地目录文件存储。"""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = os.path.realpath(base_dir)
        os.makedirs(self._base_dir, exist_ok=True)

    def _resolve(self, key: str) -> str:
        base = os.path.realpath(self._base_dir)
        candidate = os.path.realpath(os.path.join(base, key))
        if os.path.commonpath([base, candidate]) != base:
            raise ValueError(f"LocalFileStore 拒绝越界路径: {key}")
        return candidate

    def write(self, rel_path: str, data: bytes) -> str:
        abs_path = self._resolve(rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as f:
            f.write(data)
        return os.path.normpath(rel_path)

    def read(self, key: str) -> bytes:
        abs_path = self._resolve(key)
        with open(abs_path, "rb") as f:
            return f.read()

    def delete(self, key: str) -> None:
        abs_path = self._resolve(key)
        if os.path.exists(abs_path):
            os.remove(abs_path)

    def stat(self, key: str) -> Dict[str, Any]:
        abs_path = self._resolve(key)
        info = os.stat(abs_path)
        content_type, _encoding = mimetypes.guess_type(key)
        return {
            "size": info.st_size,
            "mtime": info.st_mtime,
            "content_type": content_type or "application/octet-stream",
        }
