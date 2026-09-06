"""递归字符切分器（原生重写，不 import 旧 app.runtime）。

对齐旧 ``RecursiveSplitter``：按分隔符优先级（段落→行→空格→字符）递归切分，
保留 chunk_overlap 重叠，每块追加 chunk_idx/chunk_total 元数据。
"""
from __future__ import annotations

from typing import List, Sequence

from .documents import KBDocument

_DEFAULT_SEPARATORS: List[str] = ["\n\n", "\n", " ", ""]


class RecursiveSplitter:
    """递归字符切分器（零 langchain）。"""

    def __init__(self, separators: Sequence[str] = _DEFAULT_SEPARATORS,
                 strip_whitespace: bool = True) -> None:
        self._separators = list(separators)
        self._strip = strip_whitespace

    def split(self, docs: List[KBDocument], chunk_size: int = 1000,
              chunk_overlap: int = 200) -> List[KBDocument]:
        if chunk_size <= 0:
            raise ValueError(f"chunk_size 必须 > 0（当前 {chunk_size}）")
        if chunk_overlap >= chunk_size:
            raise ValueError(f"chunk_overlap({chunk_overlap}) 必须 < chunk_size({chunk_size})")

        all_chunks: List[KBDocument] = []
        for doc in docs:
            doc_chunks = self._split_text(
                doc.text, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                separators=self._separators, strip=self._strip,
            )
            total = len(doc_chunks)
            for idx, chunk_text in enumerate(doc_chunks):
                new_meta = dict(doc.metadata or {})
                new_meta.update({"chunk_idx": idx, "chunk_total": total})
                all_chunks.append(KBDocument(text=chunk_text, metadata=new_meta))
        return all_chunks

    @classmethod
    def _split_text(cls, text: str, chunk_size: int, chunk_overlap: int,
                    separators: Sequence[str], strip: bool) -> List[str]:
        if not text:
            return []
        chosen_sep = separators[-1]
        for sep in separators:
            if sep == "":
                chosen_sep = ""
                break
            if sep in text:
                chosen_sep = sep
                break
        if chosen_sep == "":
            pieces = list(text)
        else:
            pieces = text.split(chosen_sep)

        merged: List[str] = []
        current: List[str] = []
        current_len = 0
        for piece in pieces:
            if not piece:
                continue
            piece_len = len(piece) + (len(chosen_sep) if current else 0)
            if current and current_len + piece_len > chunk_size:
                block = chosen_sep.join(current)
                if strip:
                    block = block.strip()
                if block:
                    merged.append(block)
                overlap_parts: List[str] = []
                overlap_len = 0
                for tail in reversed(current):
                    added = len(tail) + (len(chosen_sep) if overlap_parts else 0)
                    if overlap_len + added > chunk_overlap:
                        break
                    overlap_parts.insert(0, tail)
                    overlap_len += added
                current = overlap_parts
                current_len = overlap_len
                if overlap_parts:
                    current_len += sum(len(chosen_sep) for _ in range(len(overlap_parts) - 1))
            current.append(piece)
            current_len += piece_len
        if current:
            block = chosen_sep.join(current)
            if strip:
                block = block.strip()
            if block:
                merged.append(block)

        final: List[str] = []
        next_seps = separators[1:] if separators else []
        for block in merged:
            if len(block) <= chunk_size or not next_seps:
                final.append(block)
                continue
            final.extend(cls._split_text(block, chunk_size=chunk_size,
                                         chunk_overlap=chunk_overlap,
                                         separators=next_seps, strip=strip))
        return final
