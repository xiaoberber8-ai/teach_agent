"""核心数据结构。"""

from __future__ import annotations

from dataclasses import dataclass

# 书籍入库状态机：uploaded -> parsed -> indexed（任意阶段失败 -> failed）
STATUS_UPLOADED = "uploaded"
STATUS_PARSED = "parsed"
STATUS_INDEXED = "indexed"
STATUS_FAILED = "failed"
VALID_STATUSES = {
    STATUS_UPLOADED,
    STATUS_PARSED,
    STATUS_INDEXED,
    STATUS_FAILED,
}


@dataclass
class Book:
    """books 表一行：一本书的元数据与处理状态。"""

    book_id: str  # 文件 SHA-256 前 12 位，天然去重
    title: str
    source_path: str  # 用户最初提供的文件路径
    stored_path: str  # 归档后的原文件路径 data/books/{id}/original.*
    file_hash: str
    status: str
    parser: str | None = None
    page_count: int = 0
    char_count: int = 0
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Chunk:
    """一个可检索的文本块。text 为还原公式后的最终文本。"""

    chunk_id: str  # f"{book_id}:{ordinal:05d}"
    book_id: str
    ordinal: int
    text: str
    chapter: str | None
    page_start: int
    page_end: int


@dataclass
class RetrievedChunk:
    """检索命中结果。"""

    chunk_id: str
    book_id: str
    text: str
    chapter: str | None
    page_start: int
    page_end: int
    distance: float  # Chroma cosine distance（越小越相关）

    @property
    def score(self) -> float:
        """余弦相似度，越大越相关，范围约 [-1, 1]。"""
        return 1.0 - self.distance
