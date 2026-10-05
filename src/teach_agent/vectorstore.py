"""Chroma 持久化向量库连接。

单用户单机场景：一个 PersistentClient、一个 collection，
向量维度由首次写入的 embedding 决定（Qwen3-Embedding 为 1024 维）。
"""

from __future__ import annotations

from functools import lru_cache

import chromadb

from .config import CHROMA_COLLECTION, CHROMA_DIR, ensure_dirs


@lru_cache(maxsize=1)
def get_collection():
    """返回（进程内缓存的）Chroma collection，cosine 距离。"""
    ensure_dirs()
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
