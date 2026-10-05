"""书籍入库流水线（离线）：uploaded -> parsed -> indexed。

三步严格分开、状态落 SQLite，任一步失败置 failed 并记录原因：
  1. 归档：校验类型/大小 -> SHA-256 去重 -> 复制原文件到 data/books/{id}/
  2. 解析：产出 parsed.md（人工抽查用）+ 页文本
  3. 索引：切块 -> 批量 embedding -> 写 Chroma（断点续传：已存在的 chunk_id 跳过）

CLI:
    uv run python -m teach_agent.ingest <文件路径> [--parser auto|pymupdf|marker]
                                         [--reindex]
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .config import (
    BOOKS_DIR,
    EMBED_BATCH_SIZE,
    MAX_FILE_MB,
    SUPPORTED_SUFFIXES,
    ensure_dirs,
)
from .embeddings import get_embeddings
from .parsers import ParseError, ParsedDocument, parse_document
from .schema import (
    STATUS_FAILED,
    STATUS_INDEXED,
    STATUS_PARSED,
    STATUS_UPLOADED,
    Book,
    Chunk,
)
from .splitting import assign_book, build_chunks
from .store import open_store
from .vectorstore import get_collection


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# z-library 等下载站会在文件名里追加 "(z-library.sk, ...)" 噪声
_NOISE_TITLE_RE = re.compile(
    r"\s*[\(（](?:z-library|1lib|z-lib)[^)）]*[\)）]", re.IGNORECASE
)


def clean_source_title(stem: str) -> str:
    """从原始文件名推导书名：去掉下载站噪声后缀与首尾残留符号。"""
    title = _NOISE_TITLE_RE.sub("", stem).strip()
    return title.strip(" -_（(，,") or stem


def sha256_file(path: Path, buf_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(buf_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _write_parsed_md(book_dir: Path, parsed: ParsedDocument) -> Path:
    """输出带页码标记的可读 Markdown，供人工抽查解析质量。"""
    target = book_dir / "parsed.md"
    lines = [f"# {parsed.title}", ""]
    for page in parsed.pages:
        lines.append(f"<!-- page:{page.page_no} -->")
        lines.append(page.text)
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _write_chunks_jsonl(book_dir: Path, chunks: list[Chunk]) -> Path:
    target = book_dir / "chunks.jsonl"
    with target.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")
    return target


def _chunk_metadata(chunk: Chunk) -> dict:
    # Chroma metadata 值只支持基础类型；None 转空字符串
    return {
        "book_id": chunk.book_id,
        "ordinal": chunk.ordinal,
        "chapter": chunk.chapter or "",
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
    }


def _validate(source: Path) -> None:
    if not source.exists() or not source.is_file():
        raise ParseError(f"文件不存在：{source}")
    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        supported = "、".join(sorted(SUPPORTED_SUFFIXES))
        raise ParseError(f"不支持的文件类型 {source.suffix}，当前支持：{supported}")
    size_mb = source.stat().st_size / 1024 / 1024
    if size_mb > MAX_FILE_MB:
        raise ParseError(f"文件 {size_mb:.1f}MB 超过上限 {MAX_FILE_MB}MB")


def ingest_book(
    source: Path,
    parser_name: str = "auto",
    reindex: bool = False,
    title_override: str | None = None,
) -> Book:
    """执行完整入库流水线，返回最终状态的 Book。失败时抛出原异常。

    title_override：显式书名（CLI --title）；否则从原始文件名清洗得到。
    """
    _validate(source)
    ensure_dirs()

    file_hash = sha256_file(source)
    book_id = file_hash[:12]
    book_dir = BOOKS_DIR / book_id
    book_dir.mkdir(parents=True, exist_ok=True)

    # 书名优先级：--title > 清洗后的原始文件名
    source_title = title_override or clean_source_title(source.stem)

    with open_store() as store:
        existing = store.get_by_hash(file_hash)
        if existing and not reindex:
            print(f"⊙ 该书已存在（book_id={book_id}，状态={existing.status}），跳过。")
            print("  如需重新解析与索引，请加 --reindex。")
            return existing

        # ---- 步骤 1：归档（uploaded）----
        stored_path = book_dir / f"original{source.suffix.lower()}"
        shutil.copy2(source, stored_path)
        now = _now()
        book = Book(
            book_id=book_id,
            title=source_title,
            source_path=str(source.resolve()),
            stored_path=str(stored_path),
            file_hash=file_hash,
            status=STATUS_UPLOADED,
            parser=parser_name,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        store.upsert(book)
        print(f"① 已归档：{stored_path}")

        try:
            # ---- 步骤 2：解析（parsed）----
            parsed = parse_document(
                stored_path, parser_name, fallback_title=source_title
            )
            # PDF 元数据自带标题时也不允许覆盖用户显式 --title
            if title_override:
                parsed.title = title_override
            parsed_md = _write_parsed_md(book_dir, parsed)
            store.update(
                book_id,
                status=STATUS_PARSED,
                title=parsed.title,
                parser=parsed.parser,
                page_count=parsed.quality["page_count"],
                char_count=parsed.quality["char_count"],
                error=None,
            )
            print(
                f"② 解析完成（{parsed.parser}）：{parsed.quality['page_count']} 页，"
                f"{parsed.quality['char_count']} 字符，"
                f"平均 {parsed.quality['avg_chars_per_page']} 字/页，"
                f"乱码率 {parsed.quality['garbled_ratio']:.2%}"
            )
            if not parsed.page_numbers_available:
                print("  ⚠ marker 未还原出页码，来源定位将只显示章节，不显示页码。")
            print(f"  请抽查解析质量：{parsed_md}")

            # ---- 步骤 3：切块 + 索引（indexed）----
            chunks = assign_book(build_chunks(parsed.pages), book_id)
            _write_chunks_jsonl(book_dir, chunks)
            print(f"③ 共切出 {len(chunks)} 个文本块，开始向量化入库…")

            collection = get_collection()
            if reindex and existing:
                collection.delete(where={"book_id": book_id})

            done_ids = set(
                collection.get(where={"book_id": book_id}, include=[])["ids"]
            )
            pending = [chunk for chunk in chunks if chunk.chunk_id not in done_ids]
            skipped = len(chunks) - len(pending)
            if skipped:
                print(f"  断点续传：跳过已索引的 {skipped} 块")

            embeddings = get_embeddings()
            total = len(pending)
            for start in range(0, total, EMBED_BATCH_SIZE):
                batch = pending[start : start + EMBED_BATCH_SIZE]
                vectors = embeddings.embed_documents([chunk.text for chunk in batch])
                collection.add(
                    ids=[chunk.chunk_id for chunk in batch],
                    documents=[chunk.text for chunk in batch],
                    metadatas=[_chunk_metadata(chunk) for chunk in batch],
                    embeddings=[vector.tolist() for vector in vectors],
                )
                print(
                    f"\r  已入库 {min(start + EMBED_BATCH_SIZE, total)}/{total} 块",
                    end="",
                    flush=True,
                )
            print()

            store.update(book_id, status=STATUS_INDEXED, error=None)
            result = store.get(book_id)
            print(f"✓ 入库完成：book_id={book_id}，《{parsed.title}》")
            return result  # type: ignore[return-value]

        except Exception as exc:
            store.update(
                book_id,
                status=STATUS_FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
