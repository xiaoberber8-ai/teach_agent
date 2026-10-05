"""文档解析：把 PDF / TXT 变成带页码的纯文本页序列。

M1 主路径是 PyMuPDF（fitz）：纯 Python、快、能保留页码，文字版教材够用。
对公式密集的 PDF 额外提供 marker 后端（公式转 LaTeX），采用延迟导入：
marker-pdf 依赖很重，未安装时给出明确安装提示，不影响主链路。
扫描版 PDF（无文字层）在解析阶段直接识别并报错，避免向索引灌入垃圾文本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import SCAN_AVG_CHARS_PER_PAGE, SUPPORTED_SUFFIXES

# PyMuPDF 常把半角空格提取为不间断空格 U+00A0，统一规范化，
# 否则按空格切分与标题正则都会失效。
_WS_RE = re.compile(r"\xa0")


def _normalize_text(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


class ParseError(Exception):
    """解析失败（含不支持格式、文件损坏等）。"""


class UnsupportedFormatError(ParseError):
    """文件类型不在 M1 支持范围内。"""


class ScannedPDFError(ParseError):
    """PDF 没有可用文字层，需要 OCR（规划中的 M6）。"""

    def __init__(self, page_count: int, avg_chars: float):
        self.page_count = page_count
        self.avg_chars = avg_chars
        super().__init__(
            f"该 PDF 疑似扫描版（{page_count} 页，平均每页仅提取到 "
            f"{avg_chars:.1f} 个字符，阈值 {SCAN_AVG_CHARS_PER_PAGE}）。"
            "当前无文字层可供索引，请改用文字版 PDF，或等 M6 接入 OCR。"
        )


@dataclass
class Page:
    page_no: int  # 从 1 开始
    text: str


@dataclass
class ParsedDocument:
    title: str
    pages: list[Page]
    parser: str
    quality: dict = field(default_factory=dict)
    # marker 无法稳定还原页码时为 False，调用方需在 UI 提示页码不可用
    page_numbers_available: bool = True


# ---------------------------------------------------------------------------
# 入口分派
# ---------------------------------------------------------------------------

def parse_document(
    path: Path,
    parser_name: str = "auto",
    fallback_title: str | None = None,
) -> ParsedDocument:
    """按文件类型与指定后端解析。parser_name: auto/pymupdf/marker。

    fallback_title：PDF 元数据没有标题时使用（一般传原始上传文件名，
    避免归档名 original.pdf 覆盖真实书名）。
    """
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = "、".join(sorted(SUPPORTED_SUFFIXES))
        raise UnsupportedFormatError(f"暂不支持 {suffix} 文件，M1 仅支持：{supported}")

    if suffix == ".txt":
        return _parse_txt(path, fallback_title)

    # PDF
    if parser_name in ("auto", "pymupdf"):
        return _parse_pdf_pymupdf(path, fallback_title)
    if parser_name == "marker":
        return _parse_pdf_marker(path, fallback_title)
    raise ParseError(f"未知解析后端: {parser_name!r}")


# ---------------------------------------------------------------------------
# 页眉/页脚剥离：同一行模板（数字归一化后）在多数页面的首行/末行重复出现
# ---------------------------------------------------------------------------

def _line_template(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip())


def _strip_running_heads(pages: list[Page]) -> list[Page]:
    if len(pages) < 3:
        return pages

    from collections import Counter

    head_templates: Counter[str] = Counter()
    foot_templates: Counter[str] = Counter()
    for page in pages:
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        if lines:
            head_templates[_line_template(lines[0])] += 1
            if len(lines) > 1:
                foot_templates[_line_template(lines[-1])] += 1

    min_hits = max(3, int(len(pages) * 0.6))
    junk = {
        template
        for template, hits in (*head_templates.items(), *foot_templates.items())
        if hits >= min_hits
    }
    if not junk:
        return pages

    cleaned: list[Page] = []
    for page in pages:
        lines = [line for line in page.text.splitlines()]
        # 只从首尾各剥一行，防止误删正文
        if lines and _line_template(lines[0]) in junk:
            lines = lines[1:]
        if lines and _line_template(lines[-1]) in junk:
            lines = lines[:-1]
        cleaned.append(Page(page_no=page.page_no, text="\n".join(lines).strip()))
    return cleaned


# ---------------------------------------------------------------------------
# TXT：按分页符 \f 分页；没有分页符则整篇为一页（page_no=1）
# ---------------------------------------------------------------------------

def _parse_txt(path: Path, fallback_title: str | None = None) -> ParsedDocument:
    content = path.read_text(encoding="utf-8", errors="replace")
    raw_pages = content.split("\f")
    pages = [
        Page(page_no=i, text=_normalize_text(text))
        for i, text in enumerate(raw_pages, start=1)
        if text.strip()
    ]
    if not pages:
        raise ParseError("TXT 文件内容为空")
    total = sum(len(p.text) for p in pages)
    return ParsedDocument(
        title=fallback_title or path.stem,
        pages=pages,
        parser="txt",
        quality={
            "page_count": len(pages),
            "char_count": total,
            "avg_chars_per_page": round(total / len(pages), 1),
            "garbled_ratio": 0.0,
        },
    )


# ---------------------------------------------------------------------------
# PyMuPDF
# ---------------------------------------------------------------------------

def _garbled_count(text: str) -> int:
    """统计疑似乱码字符：替换符 ￼ 与 Unicode 私有区字符（CID 缺字常见落点）。"""
    return sum(
        1
        for ch in text
        if ch == "�" or ("\ue000" <= ch <= "\uf8ff")
    )


def _parse_pdf_pymupdf(path: Path, fallback_title: str | None = None) -> ParsedDocument:
    try:
        import pymupdf as fitz  # PyMuPDF（1.25 起推荐 import pymupdf）
    except ImportError as exc:
        raise ParseError("未安装 PyMuPDF：请先 uv sync") from exc

    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise ParseError(f"无法打开 PDF（文件可能已损坏）：{exc}") from exc

    try:
        meta_title = ""
        try:
            meta_title = (doc.metadata.get("title") or "").strip()
        except Exception:
            pass

        pages: list[Page] = []
        total_chars = 0
        garbled = 0
        for index, page in enumerate(doc, start=1):
            text = _normalize_text(page.get_text("text"))
            pages.append(Page(page_no=index, text=text))
            total_chars += len(text)
            garbled += _garbled_count(text)
    finally:
        doc.close()

    if not pages:
        raise ParseError("PDF 中没有任何页面")

    avg_chars = total_chars / len(pages)
    if avg_chars < SCAN_AVG_CHARS_PER_PAGE:
        raise ScannedPDFError(len(pages), avg_chars)

    pages = _strip_running_heads(pages)
    # 剥离页眉页脚后重新统计
    total_chars = sum(len(page.text) for page in pages)
    garbled = sum(_garbled_count(page.text) for page in pages)
    avg_chars = total_chars / len(pages)

    title = meta_title or fallback_title or path.stem

    garbled_ratio = round(garbled / max(total_chars, 1), 4)
    return ParsedDocument(
        title=title,
        pages=pages,
        parser="pymupdf",
        quality={
            "page_count": len(pages),
            "char_count": total_chars,
            "avg_chars_per_page": round(avg_chars, 1),
            "garbled_ratio": garbled_ratio,
        },
    )


# ---------------------------------------------------------------------------
# marker（可选后端，公式转 LaTeX；M1 未做完整实测，页码能力做降级处理）
# ---------------------------------------------------------------------------

def _parse_pdf_marker(path: Path, fallback_title: str | None = None) -> ParsedDocument:
    try:
        from marker.config.parser import ConfigParser
        from marker.converters.pdf import PdfConverter
    except ImportError as exc:
        raise ParseError(
            "未安装 marker 后端。需要时执行：uv pip install marker-pdf；"
            "否则请去掉 --parser marker，使用默认 PyMuPDF 解析。"
        ) from exc

    config_parser = ConfigParser(
        {"output_format": "markdown", "paginate_output": True}
    )
    converter = PdfConverter(config=config_parser.generate_config_dict())
    rendered = converter(str(path))
    markdown = rendered.markdown

    # marker 开启 paginate_output 后会在分页处插入形如 "{N}" 的独立行；
    # 若版本行为不同导致匹配不到页码，降级为"整篇一页"并显式标记。
    import re

    page_breaks = list(re.finditer(r"(?m)^\{(\d+)\}\s*$", markdown))
    pages: list[Page]
    page_numbers_available = True
    if page_breaks:
        pages = []
        for i, match in enumerate(page_breaks):
            start = match.end()
            end = page_breaks[i + 1].start() if i + 1 < len(page_breaks) else len(markdown)
            text = markdown[start:end].strip()
            if text:
                pages.append(Page(page_no=i + 1, text=text))
    else:
        page_numbers_available = False
        pages = [Page(page_no=1, text=markdown.strip())]

    if not pages or not pages[0].text:
        raise ParseError("marker 解析结果为空")

    total = sum(len(p.text) for p in pages)
    return ParsedDocument(
        title=fallback_title or path.stem,
        pages=pages,
        parser="marker",
        quality={
            "page_count": len(pages),
            "char_count": total,
            "avg_chars_per_page": round(total / max(len(pages), 1), 1),
            "garbled_ratio": 0.0,
            "note": "由 marker 解析，公式以 LaTeX 形式保留，请抽查 parsed.md 质量",
        },
        page_numbers_available=page_numbers_available,
    )
