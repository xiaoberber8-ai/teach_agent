"""中文教材切块。

设计要点（数学/物理教材场景）
-----------------------------
1. 公式整体保护：先用占位符替换 ``$$...$$`` / ``$...$`` / ``\\[...\\]`` /
   ``\\(...\\)``，占位符内不含任何分隔符字符，递归切分永远不会把公式切断，
   切块完成后再还原成 LaTeX 原文。
2. 页码可回溯：用单字符分页符 ``\\f`` 连接各页——单字符不可能被切碎；
   切块后通过字符偏移直接换算起始/结束页码。
3. 章节就近归属：识别"第 N 章/节"等标题行，记录其偏移，每块取最近标题。
4. 中文优先的分隔符层级：段落 -> 行 -> 句末标点 -> 句内标点 -> 空格 -> 硬切。
"""

from __future__ import annotations

import bisect
import re

from .config import CHUNK_MIN_CHARS, CHUNK_OVERLAP, CHUNK_SIZE
from .parsers import Page
from .schema import Chunk

# 公式：先匹配长的 $$...$$，再匹配行内 $...$，以及 LaTeX 括号定界
_MATH_RE = re.compile(
    r"\$\$[\s\S]+?\$\$"
    r"|\$[^\n$]{1,300}?\$"
    r"|\\\[[\s\S]+?\\\]"
    r"|\\\([\s\S]+?\\\)"
)
# 占位符形如 MATH000000，纯字母数字，不含任何分隔符
_MATH_TOKEN_RE = re.compile(r"MATH(\d{6})")
_MATH_TOKEN_PREFIX = "MATH"

# 正文页标题（严格：不允许点引线/句末标点）。
# 真实教材兼容（Mitchell 中文版踩坑）：
# - "第1 章绪论"、"第2 章 模型评估与选择"：数字与"章"间可有空格，标题可粘连；
# - "1 . 6 应用现状"、"1 2 . 6 稳定性"、"2 . 4 . 1 假设检验"：
#   节号数字内部与点号两侧都可能插空格。
# 标题的最终归一化（数字压实、章名补全、页眉去重）在 build_chunks 中完成。
_HEAD_RE = re.compile(
    r"(?m)^[ \t]{0,8}("
    r"第\s*[0-9一二三四五六七八九十百]+\s*[章讲节](?:\s*[\u4e00-\u9fff][^.\n]{0,30})?"
    r"|[一二三四五六七八九十]+、[\u4e00-\u9fff][^.\n]{0,30}"
    r"|\d[\d \t]*(?:\s*\.\s*\d[\d \t]*)+[ \u3000]+[\u4e00-\u9fff][^.\n]{0,24}"
    r")[ \t]*$"
)

# 宽松版章标题收割：允许目录点引线行（"第1 章绪论.... 14"），
# 仅用于建立 章号 -> 最完整章名 的映射，不直接进切块。
_CHAPTER_HARVEST_RE = re.compile(
    r"(?m)^[ \t]{0,8}第\s*([0-9一二三四五六七八九十百]+)\s*([章讲节])"
    r"[\s\u3000]*([\u4e00-\u9fff][^.\n．…]{0,30})"
)

# 目录页点引线：四个以上半角/全角句点，或连续省略号
_TOC_DOTLEADER_RE = re.compile(r"(?:\.{4,}|．{4,}|…{2,})")

# 标题不允许以这些字符收尾（句子/表格的典型结尾）
_HEADING_BAD_ENDCHARS = "。．.，,；;：:！!？?、 "

# 节号与其标题的分界（前半为带空格的数字点号串）
_SECTION_SPLIT_RE = re.compile(r"^([0-9.\s]+)[ \u3000]+(.+)$")
# 章标题拆解
_CHAPTER_SPLIT_RE = re.compile(
    r"第\s*([0-9一二三四五六七八九十百]+)\s*([章讲节])[\s\u3000]*(.*)$"
)


def _heading_ok(title: str) -> bool:
    """正则命中后的二次校验，剔除目录条目、表格行与正文中的假标题。"""
    text = title.strip()
    if not text or len(text) > 34:
        return False
    if _TOC_DOTLEADER_RE.search(text):
        return False
    if text[-1] in _HEADING_BAD_ENDCHARS:
        return False
    return True


def harvest_chapter_titles(pages_text: list[str]) -> dict[tuple[str, str], str]:
    """全书扫描，建立 (章号, 章/讲/节) -> 最可信章名 映射。

    信任优先级：目录点引线行（"第1 章绪论...."）> 正文行的最长标题。
    正文里会出现排版换行造成的假标题（"第1 章且有数学恐惧"），
    长度可能超过真标题，因此绝不能单纯取最长。
    """
    trusted: dict[tuple[str, str], str] = {}
    fallback: dict[tuple[str, str], str] = {}
    for text in pages_text:
        for match in _CHAPTER_HARVEST_RE.finditer(text):
            number, kind, title = (
                match.group(1),
                match.group(2),
                match.group(3).strip(),
            )
            if not title:
                continue
            key = (number, kind)
            remainder = text[match.end() : match.end() + 12]
            if _TOC_DOTLEADER_RE.search(remainder):
                if len(title) > len(trusted.get(key, "")):
                    trusted[key] = title
            elif key not in trusted and len(title) > len(fallback.get(key, "")):
                fallback[key] = title
    return {**fallback, **trusted}


def canonicalize_heading(raw: str, chapter_titles: dict[tuple[str, str], str]) -> str:
    """把各种抽取形态的标题归一为稳定显示名。"""
    text = re.sub(r"[ \t]+", " ", raw.strip())
    chapter_match = _CHAPTER_SPLIT_RE.match(text)
    if chapter_match:
        number, kind, title = chapter_match.groups()
        # 章名无条件采用可信映射：页眉可能截断，正文粘连处可能是假标题
        title = chapter_titles.get((number, kind), title.strip())
        return f"第{number}{kind} {title}".strip()
    section_match = _SECTION_SPLIT_RE.match(text)
    if section_match and re.fullmatch(r"[\d.\s]+", section_match.group(1)):
        number = re.sub(r"\s+", "", section_match.group(1))
        title = section_match.group(2).strip()
        if number.count(".") >= 1 and title:
            return f"{number} {title}"
    return text  # 一、二、… 枚举式标题原样保留


def find_page_headings(
    page_text: str, chapter_titles: dict[tuple[str, str], str]
) -> list[tuple[int, str]]:
    """单页标题探测：严格正则 + 二次校验 + 归一化。"""
    headings: list[tuple[int, str]] = []
    for match in _HEAD_RE.finditer(page_text):
        raw = match.group(1)
        if not _heading_ok(raw):
            continue
        headings.append((match.start(), canonicalize_heading(raw, chapter_titles)))
    return headings


def count_chapter_level(page_text: str) -> int:
    """目录页判定用：宽松统计一页中的章级标题数量（含点引线形态）。"""
    return len(_CHAPTER_HARVEST_RE.findall(page_text))

# 递归切分层级：顺序即优先级；"" 是最终硬切兜底
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ". ", "：", "，", ",", "\u3000", " ", ""]

# 判断碎片时需要忽略的字符（公式、空白、分页符）
_NON_CONTENT_RE = re.compile(r"\s|\f|MATH\d{6}")

# 章节切断后短于此加权长度的片视为 overlap 重复碎片，并入相邻块
_MERGE_MIN_CHARS = 120


def _restore_math(text: str, formulas: list[str]) -> str:
    def _put_back(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return formulas[index] if 0 <= index < len(formulas) else ""

    restored = _MATH_TOKEN_RE.sub(_put_back, text)
    # 极小概率下占位符被超长无分隔符文本的硬切截断，清理残片，避免污染索引
    restored = re.sub(r"MATH\d{0,5}(?![\d])", "", restored)
    return restored.replace("\f", "\n").strip()


def _build_weight_prefix(protected: str, formulas: list[str]) -> list[int]:
    """还原后文本长度的前缀和。

    普通字符权重 1；公式占位符（12 字符）整体按原 LaTeX 长度计权，
    使切块大小按"模型与用户最终看到的文本"计算，而不是被占位符缩短。
    """
    weights = [1] * len(protected)
    for match in _MATH_TOKEN_RE.finditer(protected):
        original_len = len(formulas[int(match.group(1))])
        weights[match.start()] = max(original_len, 1)
        for pos in range(match.start() + 1, match.end()):
            weights[pos] = 0
    prefix = [0] * (len(protected) + 1)
    for i, weight in enumerate(weights):
        prefix[i + 1] = prefix[i] + weight
    return prefix


def _recursive_split(
    text: str,
    max_len: int,
    weight_of=None,
) -> list[tuple[str, int]]:
    """按分隔符层级递归切分，返回 (片段, 在原文中的起始偏移)。

    分隔符保留在前一个片段的末尾（与 LangChain RecursiveCharacterSplitter
    的 keep_separator 行为一致）。weight_of(start, end) 给定时按还原后
    文本长度判断超限，否则按字符数。
    """
    def _too_long(piece: str, start: int) -> bool:
        if weight_of is not None:
            return weight_of(start, start + len(piece)) > max_len
        return len(piece) > max_len

    pieces: list[tuple[str, int]] = [(text, 0)]
    for sep in _SEPARATORS[:-1]:
        next_pieces: list[tuple[str, int]] = []
        for piece, start in pieces:
            if not _too_long(piece, start):
                next_pieces.append((piece, start))
                continue
            cursor = 0
            for match in re.finditer(re.escape(sep), piece):
                end = match.end()
                next_pieces.append((piece[cursor:end], start + cursor))
                cursor = end
            if cursor < len(piece):
                next_pieces.append((piece[cursor:], start + cursor))
        pieces = next_pieces

    # 最终兜底：没有任何分隔符的超长片段硬切（按字符步长，极端情形）
    final: list[tuple[str, int]] = []
    for piece, start in pieces:
        if not _too_long(piece, start):
            final.append((piece, start))
        else:
            for i in range(0, len(piece), max_len):
                final.append((piece[i : i + max_len], start + i))
    return [(p, s) for p, s in final if p]


def _pack(
    pieces: list[tuple[str, int]],
    size: int,
    overlap: int,
    weight_of=None,
) -> list[tuple[int, int]]:
    """贪心把原子片段打包成 [start, end) 块。

    块大小按 weight_of（还原后文本长度）控制；块间重叠按字符数回退，
    是简单但足够好的近似。
    """
    def _length(start: int, end: int) -> int:
        return weight_of(start, end) if weight_of is not None else end - start

    spans: list[tuple[int, int]] = []
    cur_start: int | None = None
    cur_end = 0
    for piece, start in pieces:
        end = start + len(piece)
        if cur_start is None:
            cur_start, cur_end = start, end
            continue
        if _length(cur_start, end) <= size:
            cur_end = end
        else:
            spans.append((cur_start, cur_end))
            # 新块从前一块尾部回退 overlap 个字符开始（片段连续，无空隙）
            cur_start = max(start, cur_end - overlap)
            cur_end = end
    if cur_start is not None:
        spans.append((cur_start, cur_end))
    return spans


def build_chunks(
    pages: list[Page],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
    min_chars: int = CHUNK_MIN_CHARS,
) -> list[Chunk]:
    """把解析后的页序列切成带页码与章节归属的 Chunk 列表。"""
    if not pages:
        return []

    # 1. 逐页做公式保护（避免跨页匹配吞掉页界），再用分页符连接。
    #    页码偏移、章节偏移全部建立在受保护文本上，保证同一偏移空间。
    formulas: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        formulas.append(match.group(0))
        return f"{_MATH_TOKEN_PREFIX}{len(formulas) - 1:06d}"

    protected_parts = [_MATH_RE.sub(_stash, page.text) for page in pages]

    # 1.4 全书收割章名（含目录页点引线行），用于页眉截断标题的归一补全
    chapter_titles = harvest_chapter_titles(protected_parts)

    # 1.5 目录页剔除（整页不进索引：没有检索价值，还会污染章节归属）：
    #     a) 一页内出现两个以上"第X章"级标题；
    #     b) 一页内出现 3 行以上点引线（"1.1 引言........ 14"式目录）。
    #     空页直接移除，另存真实页码映射，偏移空间只含保留页。
    kept_parts: list[str] = []
    kept_page_nos: list[int] = []
    for page_index, page_text in enumerate(protected_parts):
        chapter_level = count_chapter_level(page_text)
        dotleader_lines = sum(
            1 for line in page_text.splitlines() if _TOC_DOTLEADER_RE.search(line)
        )
        if chapter_level >= 2 or dotleader_lines >= 3:
            continue
        kept_parts.append(page_text)
        kept_page_nos.append(pages[page_index].page_no)
    protected_parts = kept_parts
    protected = "\f".join(protected_parts)

    page_offsets: list[int] = []
    offset = 0
    for text in protected_parts:
        page_offsets.append(offset)
        offset += len(text) + 1  # +1 是连接用的 \f

    # 2. 章节标记偏移（逐页检测 -> 全局偏移 -> 页眉重复标题去重）
    #    注意：全局文本以 \f 连页，而 (?m) 的 ^ 只在 \n 后成立，\f 后首行
    #    标题会漏匹配，因此必须逐页 finditer。
    raw_marks: list[tuple[int, str]] = []
    for page_index, page_text in enumerate(protected_parts):
        for local_start, title in find_page_headings(page_text, chapter_titles):
            raw_marks.append((page_offsets[page_index] + local_start, title))

    # 每页页眉都重复当前章/节名：相邻（中间没有其他标题）的同名标记只保留
    # 第一个，避免每个页首都产生一次强制切块。
    chapter_marks: list[tuple[int, str]] = []
    for mark_offset, title in raw_marks:
        if chapter_marks and chapter_marks[-1][1] == title:
            continue
        chapter_marks.append((mark_offset, title))
    chapter_offsets = [mark[0] for mark in chapter_marks]

    def chapter_at(pos: int) -> str | None:
        idx = bisect.bisect_right(chapter_offsets, pos) - 1
        return chapter_marks[idx][1] if idx >= 0 else None

    def dominant_chapter(span_start: int, span_end: int) -> str | None:
        """块可能因碎片合并而跨越紧邻标题，取块内文字占比最大的章节。"""
        bounds = [
            (mark_offset, title)
            for mark_offset, title in chapter_marks
            if span_start <= mark_offset < span_end
        ]
        if not bounds:
            return chapter_at(span_start)
        # 各段：(段起点, 段章节名)；首段归属沿用 start 之前最近的标题
        seg_starts = [span_start] + [mark_offset for mark_offset, _ in bounds]
        seg_titles = [chapter_at(span_start)] + [title for _, title in bounds]
        best_title: str | None = None
        best_len = -1
        for i, seg_start in enumerate(seg_starts):
            seg_end = seg_starts[i + 1] if i + 1 < len(seg_starts) else span_end
            seg_len = weight_of(seg_start, seg_end)
            if seg_len > best_len:
                best_len = seg_len
                best_title = seg_titles[i]
        return best_title

    def page_at(pos: int) -> int:
        # bisect_right 给出 pos 在保留页序列中的 1-based 槽位，
        # 再映射回原始 PDF 页码（目录页可能已被剔除）。
        slot = bisect.bisect_right(page_offsets, pos)
        slot = min(max(slot, 1), len(kept_page_nos))
        return kept_page_nos[slot - 1]

    # 3. 递归切分 + 贪心打包（按公式还原后的真实文本长度计权）
    weight_prefix = _build_weight_prefix(protected, formulas)

    def weight_of(start: int, end: int) -> int:
        return weight_prefix[end] - weight_prefix[start]

    pieces = _recursive_split(protected, chunk_size, weight_of=weight_of)
    spans = _pack(pieces, chunk_size, overlap, weight_of=weight_of)

    # 3.5 章节边界不跨块：任何落在块内的章节标题偏移都是强制切断点，
    #     保证一个块只谈一个主题，引用中的章节标签也不会误导。
    #     切断可能切出很小的 overlap 重复片（几十字），短于
    #     _MERGE_MIN_CHARS 的片并入相邻块，避免索引中出现冗余碎片。
    split_spans: list[tuple[int, int]] = []
    for span_start, span_end in spans:
        cut_positions = [
            mark_offset
            for mark_offset in chapter_offsets
            if span_start < mark_offset < span_end
        ]
        raw: list[tuple[int, int]] = []
        cursor = span_start
        for cut in cut_positions:
            raw.append((cursor, cut))
            cursor = cut
        raw.append((cursor, span_end))

        # 首片过短：并入后片（后片起点前移）
        if len(raw) > 1 and weight_of(*raw[0]) < _MERGE_MIN_CHARS:
            raw[1] = (raw[0][0], raw[1][1])
            raw = raw[1:]
        # 其余小片：并入前片
        merged: list[tuple[int, int]] = []
        for seg_start, seg_end in raw:
            if (
                merged
                and weight_of(seg_start, seg_end) < _MERGE_MIN_CHARS
            ):
                merged[-1] = (merged[-1][0], seg_end)
            else:
                merged.append((seg_start, seg_end))
        split_spans.extend(merged)
    spans = split_spans

    # 4. 还原公式、换算页码、过滤碎片、顺序编号
    chunks: list[Chunk] = []
    ordinal = 0
    for start, end in spans:
        raw = protected[start:end]
        text = _restore_math(raw, formulas)
        content_like = _NON_CONTENT_RE.sub("", raw)
        if len(content_like) < min_chars:
            continue
        page_start = min(page_at(start), len(pages))
        page_end = min(page_at(max(end - 1, start)), len(pages))
        book_id = ""  # 由 indexing 层填充并构造最终 chunk_id
        chunks.append(
            Chunk(
                chunk_id=f"{book_id}:{ordinal:05d}",
                book_id=book_id,
                ordinal=ordinal,
                text=text,
                chapter=dominant_chapter(start, end),
                page_start=page_start,
                page_end=max(page_end, page_start),
            )
        )
        ordinal += 1
    return chunks


def assign_book(chunks: list[Chunk], book_id: str) -> list[Chunk]:
    """入库前为切块补上 book_id 与全局 chunk_id。"""
    for chunk in chunks:
        chunk.book_id = book_id
        chunk.chunk_id = f"{book_id}:{chunk.ordinal:05d}"
    return chunks
