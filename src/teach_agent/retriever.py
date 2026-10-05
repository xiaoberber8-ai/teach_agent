"""语义检索：问题 -> embedding -> Chroma 带 book_id 过滤的 top-k。

低分时不硬凑答案：低于 SCORE_THRESHOLD 的片段被过滤，结果为空时由上层
（M2 的 Agent / CLI）明确告知"书中未找到"，防止幻觉。
"""

from __future__ import annotations

from .config import SCORE_THRESHOLD, TOP_K
from .embeddings import get_embeddings
from .schema import RetrievedChunk
from .vectorstore import get_collection


def search(
    query: str,
    book_id: str | None = None,
    k: int = TOP_K,
    score_threshold: float = SCORE_THRESHOLD,
) -> tuple[list[RetrievedChunk], str | None]:
    """返回 (命中片段列表, 空结果原因)。正常检索但无过阈值结果时 reason 非空。"""
    collection = get_collection()
    if collection.count() == 0:
        return [], "向量库为空：还没有任何书籍完成入库。"

    query_vector = get_embeddings().embed_query(query)
    result = collection.query(
        query_embeddings=[query_vector.tolist()],
        n_results=k,
        where={"book_id": book_id} if book_id else None,
        include=["documents", "metadatas", "distances"],
    )

    ids = result.get("ids", [[]])[0]
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    hits: list[RetrievedChunk] = []
    for chunk_id, text, metadata, distance in zip(
        ids, documents, metadatas, distances
    ):
        if distance > score_threshold:
            continue
        hits.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                book_id=metadata.get("book_id", ""),
                text=text,
                chapter=metadata.get("chapter") or None,
                page_start=int(metadata.get("page_start", 0)),
                page_end=int(metadata.get("page_end", 0)),
                distance=float(distance),
            )
        )

    if not hits:
        reason = (
            f"已在{'指定书' if book_id else '全部书'}中检索，但最高分片段也低于阈值"
            f"（distance 阈值 {score_threshold}），可视为书中未直接覆盖该问题。"
        )
        return [], reason
    return hits, None
