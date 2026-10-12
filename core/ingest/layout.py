"""PyMuPDF による文字座標の取得と、行・段落の復元（SPEC 3.3-1）。

PDF のブロックは論文によって「1段落＝1ブロック」（英語に多い）だったり「1行＝1ブロック」
（日本語に多い）だったりするため、ブロックには頼らず、行の位置・字下げ・行間・行末から段落を組み立てる。
"""

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from core.ingest.charclass import JA_LETTERS, WIDE_CHARS
from core.ingest.models import BBox, ParagraphKind

Rect = tuple[float, float, float, float]

# 柱（ヘッダー・フッター）とみなす上下の余白の割合
_MARGIN_RATIO = 0.10
_HEADING_MAX_CHARS = 80
# 「1 Introduction」「2.1 文圧縮」「A Appendix」「IV. Results」。番号の後は大文字か日本語で始まる
_NUMBERED_HEADING = re.compile(
    rf"^(?:\d{{1,2}}(?:\.\d{{1,2}}){{0,3}}\.?\s*(?=[A-Z{JA_LETTERS}])"
    r"|[A-H](?:\.\d{1,2}){0,2}\.?\s+(?=[A-Z])"
    r"|[IVX]{1,4}\.\s+(?=[A-Z]))"
)
_UNNUMBERED_HEADINGS = re.compile(
    r"^(?:abstract|references|bibliography|acknowledge?ments?|appendix|"
    r"biography|biographies|概要|要旨|あらまし|参考文献|引用文献|文献|謝辞|付録|略歴|著者紹介)$",
    re.IGNORECASE,
)
_CAPTION = re.compile(r"^(?:図|表|Figure|Fig\.|Table)\s*[0-9０-９A-Z]+", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[。．.!?！？:：)）」』]$")
# 独自エンコーディングのフォントで化けた文字列（図中の文字など）の検出用
_SYMBOLS = re.compile(r"[!\"#$%&'()*+,\-/<=>@\\^_`{|}~]")
_WIDE_CHAR = re.compile(f"[{WIDE_CHARS}]")
# 見出しに使われる太字・ゴシック体のフォント名（フラグが立っていない PDF もあるため名前でも見る）
_BOLD_FONT = re.compile(r"bold|medi|semibold|heavy|black|gothic|goth", re.IGNORECASE)


@dataclass
class Char:
    c: str
    page: int
    bbox: Rect | None  # 行の結合で補った空白は None
    line: int  # 通し番号（同じ行の文字をまとめて矩形にするため）


@dataclass
class Line:
    index: int
    page: int
    bbox: Rect
    chars: list[Char]
    size: float
    bold: bool
    page_width: float
    page_height: float

    @property
    def text(self) -> str:
        return "".join(c.c for c in self.chars)


@dataclass
class RawParagraph:
    kind: ParagraphKind
    lines: list[Line]
    chars: list[Char] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(c.c for c in self.chars)

    @property
    def page(self) -> int:
        return self.lines[0].page

    def bboxes(self) -> list[BBox]:
        return [
            BBox(page=ln.page, x0=ln.bbox[0], y0=ln.bbox[1], x1=ln.bbox[2], y1=ln.bbox[3])
            for ln in self.lines
        ]


@dataclass
class Layout:
    page_count: int
    paragraphs: list[RawParagraph]
    body_size: float


def extract_lines(pdf: pymupdf.Document) -> list[Line]:
    """全ページの行を、文字ごとの座標つきで取り出す（PDF 内の記述順）。"""
    lines: list[Line] = []
    for page_index in range(pdf.page_count):
        page = pdf[page_index]
        page_no = page_index + 1
        # 合字（ﬀ・ﬁ など）は通常の文字に展開する（引用の照合で "Raﬀel" と "Raffel" を一致させるため）
        raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
        for block in raw["blocks"]:
            if block.get("type") != 0:
                continue
            for ln in block["lines"]:
                # 縦書き・回転した行は本文として扱わない
                if abs(ln["dir"][1]) > 0.1:
                    continue
                chars: list[Char] = []
                sizes: list[float] = []
                bold_chars = 0
                for span in ln["spans"]:
                    for ch in span["chars"]:
                        chars.append(Char(ch["c"], page_no, tuple(ch["bbox"]), len(lines)))
                        sizes.append(span["size"])
                        if span["flags"] & pymupdf.TEXT_FONT_BOLD or _BOLD_FONT.search(
                            span["font"]
                        ):
                            bold_chars += 1
                text = "".join(c.c for c in chars).strip()
                if not text:
                    continue
                lines.append(
                    Line(
                        index=len(lines),
                        page=page_no,
                        bbox=tuple(ln["bbox"]),
                        chars=chars,
                        size=statistics.median(sizes),
                        bold=bold_chars > len(chars) / 2,
                        page_width=page.rect.width,
                        page_height=page.rect.height,
                    )
                )
    return lines


def merge_same_baseline(lines: list[Line]) -> list[Line]:
    """同じ高さに並ぶ行（見出しの番号と本文が別の行になっている場合など）を1行にまとめる。"""
    merged: list[Line] = []
    for ln in lines:
        prev = merged[-1] if merged else None
        if prev is not None and prev.page == ln.page:
            overlap = min(prev.bbox[3], ln.bbox[3]) - max(prev.bbox[1], ln.bbox[1])
            height = min(prev.bbox[3] - prev.bbox[1], ln.bbox[3] - ln.bbox[1])
            gap = ln.bbox[0] - prev.bbox[2]
            if height > 0 and overlap > height * 0.6 and -1.0 <= gap < max(prev.size, 1.0) * 1.5:
                joiner = [Char(" ", ln.page, None, prev.index)] if gap > prev.size * 0.2 else []
                for ch in ln.chars:
                    ch.line = prev.index
                prev.chars.extend(joiner + ln.chars)
                prev.bbox = (
                    min(prev.bbox[0], ln.bbox[0]),
                    min(prev.bbox[1], ln.bbox[1]),
                    max(prev.bbox[2], ln.bbox[2]),
                    max(prev.bbox[3], ln.bbox[3]),
                )
                prev.bold = prev.bold or ln.bold
                continue
        merged.append(ln)
    return merged


def _normalize_running(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", "", text))


def remove_running_heads(lines: list[Line], page_count: int) -> list[Line]:
    """柱（各ページに繰り返し現れるヘッダー・フッター）とページ番号を取り除く。"""

    def in_margin(ln: Line) -> bool:
        return ln.bbox[3] < ln.page_height * _MARGIN_RATIO or ln.bbox[1] > ln.page_height * (
            1 - _MARGIN_RATIO
        )

    pages_by_text: dict[str, set[int]] = {}
    for ln in lines:
        if in_margin(ln):
            pages_by_text.setdefault(_normalize_running(ln.text), set()).add(ln.page)
    threshold = max(2, int(page_count * 0.3))
    repeated = {t for t, pages in pages_by_text.items() if len(pages) >= threshold}

    def is_running(ln: Line) -> bool:
        if not in_margin(ln):
            return False
        norm = _normalize_running(ln.text)
        return norm in repeated or re.fullmatch(r"[-–—(（]*#[-–—)）]*", norm) is not None

    return [ln for ln in lines if not is_running(ln)]


@dataclass(frozen=True)
class _Column:
    left: float
    right: float


def _columns(lines: list[Line], body_size: float) -> dict[tuple[int, int], _Column]:
    """ページ・段（0=左または1段組、1=右）ごとの本文の左端・右端。"""
    groups: dict[tuple[int, int], list[Line]] = {}
    for ln in lines:
        if abs(ln.size - body_size) <= 1.0:
            groups.setdefault((ln.page, column_of(ln)), []).append(ln)
    result: dict[tuple[int, int], _Column] = {}
    for key, members in groups.items():
        lefts = sorted(round(m.bbox[0]) for m in members)
        rights = sorted(round(m.bbox[2]) for m in members)
        # 最頻の左端（字下げしていない行）と、上位の右端（行末まで埋まった行）
        left = Counter(lefts).most_common(1)[0][0]
        right = rights[int(len(rights) * 0.9) - 1] if len(rights) >= 2 else rights[-1]
        result[key] = _Column(float(left), float(right))
    return result


def column_of(ln: Line) -> int:
    width = ln.bbox[2] - ln.bbox[0]
    mid = ln.page_width / 2
    if width > ln.page_width * 0.55 or ln.bbox[0] < mid <= ln.bbox[2] - width * 0.1:
        return 0
    return 0 if (ln.bbox[0] + ln.bbox[2]) / 2 < mid else 1


def _is_heading(ln: Line, body_size: float, use_bold: bool) -> bool:
    text = ln.text.strip()
    if len(text) > _HEADING_MAX_CHARS or not text:
        return False
    if _UNNUMBERED_HEADINGS.match(re.sub(r"\s+", "", text)):
        return True
    numbered = _NUMBERED_HEADING.match(text)
    if numbered and not _SENTENCE_END.search(text):
        # 表のセル（"3.8M"、"7 H 333 USD"、"28 万" など）を除く：番号の後に2文字以上の語が必要
        rest = text[numbered.end() :]
        first = rest.split()[0] if rest.split() else ""
        letters = sum(1 for ch in rest if ch.isalpha())
        if letters < 2 or (len(first) < 2 and not _WIDE_CHAR.match(first)):
            return False
        # 番号付きの行でも、本文と同じ大きさ・太さのもの（箇条書き・表のセル等）は除く
        return (use_bold and ln.bold) or ln.size >= body_size + 0.5
    return False


def _join(prev: list[Char], nxt: Line) -> list[Char]:
    """行をつなぐ。英語のハイフネーションを戻し、英語どうしの間には空白を補う。"""
    prev_text = "".join(c.c for c in prev).rstrip()
    next_text = nxt.text.lstrip()
    if not prev_text or not next_text:
        return []
    last, first = prev_text[-1], next_text[0]
    if last == "-" and len(prev_text) >= 2 and prev_text[-2].isalpha() and first.islower():
        # 行末のハイフンを取り除く
        for i in range(len(prev) - 1, -1, -1):
            if prev[i].c == "-":
                del prev[i]
                break
        return []
    if _WIDE_CHAR.match(last) or _WIDE_CHAR.match(first):
        return []
    return [Char(" ", nxt.page, None, nxt.index)]


def build_paragraphs(lines: list[Line]) -> tuple[list[RawParagraph], float]:
    if not lines:
        return [], 10.0
    body_size = statistics.median(ln.size for ln in lines for _ in ln.chars)
    # 本文の大半が太字・ゴシック体の文書では、太さを見出しの手がかりにしない
    bold_chars = sum(len(ln.chars) for ln in lines if ln.bold)
    use_bold = bold_chars < sum(len(ln.chars) for ln in lines) / 2
    columns = _columns(lines, body_size)
    paragraphs: list[RawParagraph] = []
    current: list[Line] = []

    def flush() -> None:
        if current:
            kind = _kind(current, body_size, use_bold)
            paragraphs.append(RawParagraph(kind=kind, lines=list(current)))
            current.clear()

    for ln in lines:
        if _is_heading(ln, body_size, use_bold):
            flush()
            current.append(ln)
            flush()
            continue
        if current and _starts_new_paragraph(current[-1], ln, columns, body_size):
            flush()
        current.append(ln)
    flush()

    for para in paragraphs:
        chars: list[Char] = []
        for i, ln in enumerate(para.lines):
            if i > 0:
                chars.extend(_join(chars, ln))
            chars.extend(_strip_chars(ln.chars))
        para.chars = chars
    return paragraphs, body_size


def _strip_chars(chars: list[Char]) -> list[Char]:
    start, end = 0, len(chars)
    while start < end and chars[start].c.isspace():
        start += 1
    while end > start and chars[end - 1].c.isspace():
        end -= 1
    return chars[start:end]


def _starts_new_paragraph(
    prev: Line, ln: Line, columns: dict[tuple[int, int], _Column], body_size: float
) -> bool:
    if abs(prev.size - ln.size) > 1.0:
        return True
    em = max(ln.size, 1.0)
    col = columns.get((ln.page, column_of(ln)))
    prev_col = columns.get((prev.page, column_of(prev)))
    indented = col is not None and ln.bbox[0] > col.left + em * 0.6
    prev_short = prev_col is not None and prev.bbox[2] < prev_col.right - em * 2
    same_flow = ln.page == prev.page and column_of(ln) == column_of(prev)
    if same_flow:
        gap = ln.bbox[1] - prev.bbox[3]
        if gap > em * 0.9 or gap < -em * 0.5:  # 行間が空いた、または上に戻った
            return True
    return indented or prev_short


def _kind(lines: list[Line], body_size: float, use_bold: bool) -> ParagraphKind:
    text = "".join(ln.text for ln in lines).strip()
    if len(lines) == 1 and _is_heading(lines[0], body_size, use_bold):
        return "heading"
    if _CAPTION.match(text):
        return "caption"
    visible = [ch for ch in text if not ch.isspace()]
    if visible and len(_SYMBOLS.findall(text)) / len(visible) > 0.25:
        return "fragment"
    if len(text) < 30 and not _SENTENCE_END.search(text):
        return "fragment"
    return "body"


def analyze_layout(path: str | Path) -> Layout:
    with pymupdf.open(path) as pdf:
        page_count = pdf.page_count
        lines = extract_lines(pdf)
    lines = remove_running_heads(merge_same_baseline(lines), page_count)
    paragraphs, body_size = build_paragraphs(lines)
    return Layout(page_count=page_count, paragraphs=paragraphs, body_size=body_size)


def split_hanging_entries(paragraphs: list[RawParagraph]) -> list[str]:
    """参考文献節を項目に分ける。

    参考文献はぶら下げインデント（1行目が左端、2行目以降が字下げ）で組まれることが多いため、
    段落ではなく行の左端の位置で項目の区切りを判定する。
    """
    lines = [ln for p in paragraphs for ln in p.lines]
    if not lines:
        return []
    left: dict[tuple[int, int], float] = {}
    for ln in lines:
        key = (ln.page, column_of(ln))
        left[key] = min(left.get(key, ln.bbox[0]), ln.bbox[0])
    entries: list[list[Char]] = []
    for ln in lines:
        flush = ln.bbox[0] <= left[(ln.page, column_of(ln))] + max(ln.size, 1.0) * 0.5
        if flush or not entries:
            entries.append(_strip_chars(ln.chars))
        else:
            entries[-1].extend(_join(entries[-1], ln))
            entries[-1].extend(_strip_chars(ln.chars))
    return ["".join(c.c for c in e) for e in entries if e]


def char_boxes(chars: list[Char]) -> list[BBox]:
    """文字列（文）の矩形を、行ごとにまとめて返す。"""
    boxes: dict[int, list[float]] = {}
    pages: dict[int, int] = {}
    for ch in chars:
        if ch.bbox is None or ch.c.isspace():
            continue
        x0, y0, x1, y1 = ch.bbox
        if ch.line not in boxes:
            boxes[ch.line] = [x0, y0, x1, y1]
            pages[ch.line] = ch.page
        else:
            b = boxes[ch.line]
            boxes[ch.line] = [min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)]
    return [
        BBox(page=pages[k], x0=b[0], y0=b[1], x1=b[2], y1=b[3]) for k, b in sorted(boxes.items())
    ]
