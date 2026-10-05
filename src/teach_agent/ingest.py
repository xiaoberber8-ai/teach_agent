"""入库命令行入口。

用法：
    uv run python -m teach_agent.ingest ~/books/高中数学必修一.pdf
    uv run python -m teach_agent.ingest ~/books/formula_heavy.pdf --parser marker
    uv run python -m teach_agent.ingest ~/books/x.pdf --reindex
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .indexing import ingest_book
from .parsers import ParseError, ScannedPDFError


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m teach_agent.ingest",
        description="把学习书籍解析、切块并写入本地向量库",
    )
    parser.add_argument("source", type=Path, help="书籍文件路径（PDF / TXT）")
    parser.add_argument(
        "--parser",
        choices=["auto", "pymupdf", "marker"],
        default="auto",
        help="解析后端：auto 等同 pymupdf；公式密集 PDF 可试 marker（需额外安装）",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="对同一本书强制重新解析并重建索引（先删该书旧向量再写）",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="显式指定书名（默认从文件名自动清洗；PDF 元数据标题不会覆盖它）",
    )
    args = parser.parse_args()

    try:
        ingest_book(
            args.source.resolve(),
            parser_name=args.parser,
            reindex=args.reindex,
            title_override=args.title,
        )
    except ScannedPDFError as exc:
        print(f"\n✗ 扫描版 PDF：{exc}", file=sys.stderr)
        return 2
    except ParseError as exc:
        print(f"\n✗ 入库失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI 顶层兜底，状态已在流水线内置 failed
        print(f"\n✗ 未预期的错误（书籍状态已标记为 failed）：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
