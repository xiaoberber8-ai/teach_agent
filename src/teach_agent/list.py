"""书架查看入口。

用法：uv run python -m teach_agent.list
"""

from __future__ import annotations

from .store import open_store

_STATUS_LABEL = {
    "uploaded": "已上传",
    "parsed": "已解析",
    "indexed": "已索引",
    "failed": "失败",
}


def main() -> int:
    with open_store() as store:
        books = store.list_books()
    if not books:
        print("书架为空：先用 python -m teach_agent.ingest <文件> 入库一本书。")
        return 0

    print(f"共 {len(books)} 本书：\n")
    for book in books:
        label = _STATUS_LABEL.get(book.status, book.status)
        print(f"[{label}] {book.book_id}  《{book.title}》")
        print(f"       {book.page_count} 页 · {book.char_count} 字符 · 解析器 {book.parser or '-'}")
        if book.error:
            print(f"       错误：{book.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
