"""检索自测命令行入口（M1 阶段不接 LLM，只验证"问题能否找到正确片段"）。

用法：
    uv run python -m teach_agent.search "已知三角形两边和夹角，怎么求第三边？"
    uv run python -m teach_agent.search "正弦定理的内容是什么" --book-id abc123 -k 4
    uv run python -m teach_agent.list  # 查看书架
"""

from __future__ import annotations

import argparse

from .retriever import search
from .store import open_store


def _format_pages(page_start: int, page_end: int) -> str:
    if page_start <= 0:
        return "页码未知"
    if page_start == page_end:
        return f"p.{page_start}"
    return f"p.{page_start}-{page_end}"


def run_search(query: str, book_id: str | None, k: int, threshold: float) -> int:
    with open_store() as store:
        titles = {book.book_id: book.title for book in store.list_books()}

    hits, reason = search(query, book_id=book_id, k=k, score_threshold=threshold)
    print(f"\n问题：{query}")
    if reason:
        print(f"未检索到内容：{reason}")
        return 1

    print(f"命中 {len(hits)} 个片段（按相关度排序）：")
    for index, hit in enumerate(hits, start=1):
        title = titles.get(hit.book_id, hit.book_id)
        location = f"《{title}》"
        if hit.chapter:
            location += f" {hit.chapter}"
        location += f" {_format_pages(hit.page_start, hit.page_end)}"
        preview = hit.text.replace("\n", " ")
        if len(preview) > 220:
            preview = preview[:220] + "…"
        print(f"\n[{index}] 相似度={hit.score:.3f} {location}")
        print(f"    {preview}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m teach_agent.search",
        description="不入 LLM，直接打印语义检索命中的书中片段",
    )
    parser.add_argument("query", help="你的问题")
    parser.add_argument("--book-id", default=None, help="限定在某本书内检索")
    parser.add_argument("-k", type=int, default=None, help="返回片段数（默认读配置）")
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="distance 阈值（默认读 TEACH_SCORE_THRESHOLD）",
    )
    args = parser.parse_args()

    from .config import SCORE_THRESHOLD, TOP_K

    return run_search(
        query=args.query,
        book_id=args.book_id,
        k=args.k or TOP_K,
        threshold=args.threshold if args.threshold is not None else SCORE_THRESHOLD,
    )


if __name__ == "__main__":
    raise SystemExit(main())
