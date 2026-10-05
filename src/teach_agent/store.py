"""SQLite 元数据库：书籍状态机持久化（标准库 sqlite3，零额外依赖）。

记录的是"书的处理状态"，不存切块内容——切块的持久化靠 chunks.jsonl
和 Chroma。这样重新索引时只需按 book_id 清理两处即可。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .config import DB_PATH, ensure_dirs
from .schema import Book, VALID_STATUSES

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS books (
    book_id      TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    source_path  TEXT NOT NULL,
    stored_path  TEXT NOT NULL,
    file_hash    TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL,
    parser       TEXT,
    page_count   INTEGER NOT NULL DEFAULT 0,
    char_count   INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
)
"""

_BOOK_COLUMNS = (
    "book_id",
    "title",
    "source_path",
    "stored_path",
    "file_hash",
    "status",
    "parser",
    "page_count",
    "char_count",
    "error",
    "created_at",
    "updated_at",
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_book(row: sqlite3.Row) -> Book:
    return Book(**{col: row[col] for col in _BOOK_COLUMNS})


class MetaStore:
    """薄封装：只暴露 teach_agent 需要的几个方法。"""

    def __init__(self, db_path: Path | str = DB_PATH):
        ensure_dirs()
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(_CREATE_SQL)
        self.conn.commit()

    def upsert(self, book: Book) -> None:
        placeholders = ", ".join("?" for _ in _BOOK_COLUMNS)
        self.conn.execute(
            f"INSERT OR REPLACE INTO books ({', '.join(_BOOK_COLUMNS)}) "
            f"VALUES ({placeholders})",
            [getattr(book, col) for col in _BOOK_COLUMNS],
        )
        self.conn.commit()

    def get(self, book_id: str) -> Book | None:
        row = self.conn.execute(
            f"SELECT {', '.join(_BOOK_COLUMNS)} FROM books WHERE book_id = ?",
            (book_id,),
        ).fetchone()
        return _row_to_book(row) if row else None

    def get_by_hash(self, file_hash: str) -> Book | None:
        row = self.conn.execute(
            f"SELECT {', '.join(_BOOK_COLUMNS)} FROM books WHERE file_hash = ?",
            (file_hash,),
        ).fetchone()
        return _row_to_book(row) if row else None

    def list_books(self) -> list[Book]:
        rows = self.conn.execute(
            f"SELECT {', '.join(_BOOK_COLUMNS)} FROM books ORDER BY created_at DESC"
        ).fetchall()
        return [_row_to_book(row) for row in rows]

    def update(self, book_id: str, **fields: object) -> None:
        if "status" in fields and fields["status"] not in VALID_STATUSES:
            raise ValueError(f"非法状态: {fields['status']!r}")
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{col} = ?" for col in fields)
        self.conn.execute(
            f"UPDATE books SET {assignments} WHERE book_id = ?",
            [*fields.values(), book_id],
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


@contextmanager
def open_store() -> Iterator[MetaStore]:
    store = MetaStore()
    try:
        yield store
    finally:
        store.close()
