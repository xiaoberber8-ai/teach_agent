"""M2 Agent 工具集：书架查询、教材语义检索、原文复读、受控网络检索。

设计原则：
- 工具只做"取证"，所有结论由 LLM 基于工具返回的原文给出；
- 检索为空时返回结构化的原因字符串，引导模型明说"书里没讲到"，而不是硬答；
- web_search 默认与教材主链路隔离，仅供用户明确要求的书外拓展使用。
"""

from __future__ import annotations

from langchain_core.tools import tool

from .config import TAVILY_API_KEY, TOP_K
from .retriever import search
from .store import open_store
from .vectorstore import get_collection


def _title_of(book_id: str) -> str:
    """查书名；查不到时退回 book_id，保证引用行永远可读。"""
    try:
        with open_store() as store:
            book = store.get(book_id)
        return book.title if book else book_id
    except Exception:
        return book_id


@tool
def list_books() -> str:
    """列出本地书架中所有已完成索引、当前可以提问的教材。

    用户没有明确指哪本书、或你不确定书库中有没有某本书时，先调用本工具。

    Returns:
        可读的书目清单：book_id、书名、页数、字符数。
    """
    with open_store() as store:
        books = store.list_books()
    indexed = [book for book in books if book.status == "indexed"]
    if not indexed:
        return "书架空空：还没有任何教材完成入库。请先用 python -m teach_agent.ingest 入库。"
    lines = [f"书架中共有 {len(indexed)} 本已索引教材："]
    for book in indexed:
        lines.append(
            f"- book_id={book.book_id}｜《{book.title}》"
            f"｜{book.page_count} 页｜{book.char_count} 字符"
        )
    lines.append("后续 search_book 可用 book_id 限定只在某本书中检索。")
    return "\n".join(lines)


def _format_hit(rank: int, hit) -> str:
    title = _title_of(hit.book_id)
    pages = (
        f"第{hit.page_start}页"
        if hit.page_start == hit.page_end
        else f"第{hit.page_start}-{hit.page_end}页"
    )
    chapter = f"｜{hit.chapter}" if hit.chapter else ""
    return (
        f"[{rank}] 《{title}》{chapter}｜{pages}"
        f"｜相似度 {hit.score:.3f}｜chunk_id={hit.chunk_id}\n"
        f"{hit.text.strip()}"
    )


@tool
def search_book(query: str, book_id: str = "") -> str:
    """在本地教材中做语义检索（中文），返回最相关的原文片段，必须先检索再回答。

    一次检索没找到不代表书里没有：可以换一组关键词（同义概念、英文术语、
    相关符号）再检索 1-2 次；仍无结果时，如实告诉用户"这套教材里没有直接讲到"，
    绝不能用自己的记忆补一个书中没有的答案。

    Args:
        query: 用自然语言描述的问题或概念，尽量具体（可包含术语、符号、定理名）。
        book_id: 可选，限定只在某本书内检索；为空则检索整个书架。

    Returns:
        命中的原文片段列表（含书名、章节、页码、chunk_id、相似度）；
        无过阈值结果时返回以"未检索到"开头的原因说明。
    """
    hits, reason = search(query, book_id=book_id or None, k=TOP_K)
    if reason:
        return (
            f"未检索到相关内容。{reason}\n"
            "处理建议：① 换同义关键词或英文术语重试 search_book；"
            "② 用 list_books 确认该书是否已入库；"
            "③ 若确属书外内容，直接告诉用户书中未覆盖，不要编造。"
        )
    header = f"检索到 {len(hits)} 个相关片段（按相关度排序）："
    body = "\n\n".join(_format_hit(i, hit) for i, hit in enumerate(hits, start=1))
    return f"{header}\n{body}"


@tool
def read_chunk(chunk_id: str, include_adjacent: bool = False) -> str:
    """按 chunk_id 读取某个片段的完整原文，可连同前后相邻片段一起返回。

    适用场景：search_book 的结果在片段中间截断、定理证明/例题跨块，
    你需要完整上下文时再调用；普通问答不需要复读。

    Args:
        chunk_id: search_book 返回的片段标识，形如 9334b00cf91f:00002。
        include_adjacent: 为 true 时一并返回同一本书中前后各一个片段，
            用于查看跨页的完整推导。

    Returns:
        片段全文及其书名、章节、页码；id 不存在时返回错误说明。
    """
    collection = get_collection()
    result = collection.get(ids=[chunk_id], include=["documents", "metadatas"])
    if not result["ids"]:
        return f"错误：chunk_id {chunk_id!r} 不存在（可能该书已被重新索引，请重新 search_book）。"

    ids = [chunk_id]
    if include_adjacent:
        book_id, ordinal_text = chunk_id.rsplit(":", 1)
        try:
            ordinal = int(ordinal_text)
        except ValueError:
            ordinal = 0
        ids = [
            f"{book_id}:{ordinal - 1:05d}",
            chunk_id,
            f"{book_id}:{ordinal + 1:05d}",
        ]
        result = collection.get(ids=ids, include=["documents", "metadatas"])

    lines = []
    for cid, document, metadata in zip(
        result["ids"], result["documents"], result["metadatas"]
    ):
        pages = (
            f"第{metadata.get('page_start')}页"
            if metadata.get("page_start") == metadata.get("page_end")
            else f"第{metadata.get('page_start')}-{metadata.get('page_end')}页"
        )
        chapter = metadata.get("chapter") or "未分章"
        lines.append(
            f"### chunk_id={cid}｜《{_title_of(metadata.get('book_id', ''))}》"
            f"｜{chapter}｜{pages}\n{document.strip()}"
        )
    return "\n\n".join(lines)


@tool
def web_search(query: str) -> str:
    """检索教材之外的网络资料（Tavily）。

    仅在以下情形使用：用户明确说"上网查/最新进展/课外拓展/实际应用"，
    或问题明显不属于已入库教材的覆盖范围且用户仍希望了解。
    教材内能回答的学习问题一律用 search_book，不要用网络结果替代书本依据；
    使用网络资料作答时必须与教材内容分开陈述并附上来源 URL。

    Args:
        query: 网络检索词，建议包含关键术语与年份（若关心时效性）。

    Returns:
        网络结果摘要（标题、URL、正文摘录）；未配置 TAVILY_API_KEY 时返回提示。
    """
    if not TAVILY_API_KEY:
        return "未配置 TAVILY_API_KEY，网络检索不可用。请告知用户这是本地教材答疑助手。"
    try:
        from tavily import TavilyClient
    except ImportError:
        return "网络检索依赖未安装（tavily-python）。"

    client = TavilyClient(api_key=TAVILY_API_KEY)
    results = client.search(query, max_results=3, search_depth="basic")
    entries = results.get("results", [])
    if not entries:
        return f"网络上没有检索到与 {query!r} 相关的结果。"
    blocks = [f"网络检索到 {len(entries)} 条结果（注意：这不是教材内容，引用时须给出 URL）："]
    for item in entries:
        blocks.append(
            f"## {item.get('title', '')}\nURL: {item.get('url', '')}\n"
            f"{item.get('content', '')[:1200]}"
        )
    return "\n\n".join(blocks)
