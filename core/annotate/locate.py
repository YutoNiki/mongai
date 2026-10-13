"""指摘の位置（ハイライト）を、PDF 上の引用箇所に合わせる。

PDF によっては文字の並び順が特殊で、文の座標が引用箇所とずれることがある。解析の最後に、
座標が指す範囲に引用箇所の文字が本当にあるかを確かめ、無ければ PDF の中から引用箇所を探して座標を置き換える。

- exact：座標の範囲に引用箇所がある（範囲内で見つかれば、引用箇所の位置まで絞る）
- relocated：別の位置で見つけたので、座標を置き換えた
- unverified：見つからなかった（元の座標のまま）
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

import pymupdf

from core.analyze.models import Finding
from core.ingest.models import BBox

Status = Literal["exact", "relocated", "unverified"]
_SPACE_OR_HYPHEN = re.compile(r"[\s\-‐]+")
_PROBE = 14  # 引用箇所の書き出し・末尾として探す文字数


def _norm(text: str) -> str:
    return _SPACE_OR_HYPHEN.sub("", text)


def _rect(b: BBox) -> pymupdf.Rect:
    return pymupdf.Rect(b.x0 - 1, b.y0 - 1, b.x1 + 1, b.y1 + 1)


def _bbox(page_no: int, r: pymupdf.Rect) -> BBox:
    return BBox(page=page_no, x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1)


def _text_in(doc: pymupdf.Document, boxes: list[BBox]) -> str:
    return "".join(
        str(doc[b.page - 1].get_textbox(_rect(b))) for b in boxes if 1 <= b.page <= doc.page_count
    )


def _search(
    page: pymupdf.Page, needle: str, clip: pymupdf.Rect | None = None
) -> list[pymupdf.Rect]:
    if not needle.strip():
        return []
    return [pymupdf.Rect(r) for r in page.search_for(needle, clip=clip)]


def _span(page: pymupdf.Page, start: pymupdf.Rect, end: pymupdf.Rect) -> list[pymupdf.Rect]:
    """書き出しから末尾までの行の矩形（同じ段の行だけ）。"""
    if end.y1 < start.y0:  # 末尾が書き出しより上にある（段が変わった）場合は、両端だけ
        return [start, end]
    rects: list[pymupdf.Rect] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            r = pymupdf.Rect(line["bbox"])
            same_column = r.x0 < max(start.x1, end.x1) and r.x1 > min(start.x0, end.x0)
            if same_column and r.y1 > start.y0 + 1 and r.y0 < end.y1 - 1:
                rects.append(r)
    if not rects:
        return [start, end]
    # 書き出しの行は書き出しの位置から、末尾の行は末尾の位置まで
    rects.sort(key=lambda r: r.y0)
    rects[0] = pymupdf.Rect(max(rects[0].x0, start.x0), rects[0].y0, rects[0].x1, rects[0].y1)
    rects[-1] = pymupdf.Rect(rects[-1].x0, rects[-1].y0, min(rects[-1].x1, end.x1), rects[-1].y1)
    return rects


def _find_on_page(
    page: pymupdf.Page, quote: str, clip: pymupdf.Rect | None = None
) -> list[pymupdf.Rect]:
    """引用箇所をページ上で探す。そのまま見つからなければ、書き出しと末尾を探して間の行を含める。"""
    hits = _search(page, quote, clip)
    if hits:
        return hits
    compact = " ".join(quote.split())
    if len(compact) <= _PROBE * 2:
        return []
    head = _search(page, compact[:_PROBE], clip)
    tail = _search(page, compact[-_PROBE:], clip)
    if not head:
        return []
    if not tail:
        return head[:1]
    end = next((t for t in tail if t.y0 >= head[0].y0 - 1), tail[0])
    return _span(page, head[0], end)


@dataclass(frozen=True)
class Located:
    bboxes: list[BBox]
    page: int
    status: Status


def locate(doc: pymupdf.Document, finding: Finding) -> Located:
    quote = finding.quote.strip()
    probe = _norm(quote)[:_PROBE]
    if not probe:
        return Located(finding.bboxes, finding.page, "unverified")

    # 1. 今の座標の範囲に引用箇所があるか
    if probe in _norm(_text_in(doc, finding.bboxes)):
        for page_no in sorted({b.page for b in finding.bboxes}):
            boxes = [b for b in finding.bboxes if b.page == page_no]
            clip = _rect(boxes[0])
            for b in boxes[1:]:
                clip |= _rect(b)
            hits = _find_on_page(doc[page_no - 1], quote, clip)
            if hits:
                return Located([_bbox(page_no, r) for r in hits], page_no, "exact")
        return Located(finding.bboxes, finding.page, "exact")

    # 2. 近いページから順に、引用箇所を探す
    start = finding.page
    order = sorted(range(1, doc.page_count + 1), key=lambda p: (abs(p - start), p))
    for page_no in order:
        hits = _find_on_page(doc[page_no - 1], quote)
        if hits:
            return Located([_bbox(page_no, r) for r in hits], page_no, "relocated")
    return Located(finding.bboxes, finding.page, "unverified")


def relocate_findings(pdf: bytes, findings: list[Finding]) -> tuple[list[Finding], Counter[str]]:
    """すべての指摘の位置を確かめ、必要なら置き換える。判定ごとの件数も返す（本文は含まない）。"""
    counts: Counter[str] = Counter()
    result: list[Finding] = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for f in findings:
            located = locate(doc, f)
            counts[located.status] += 1
            result.append(f.model_copy(update={"bboxes": located.bboxes, "page": located.page}))
    return result, counts


def summary(counts: Counter[str]) -> str | None:
    """解析の警告に出す要約。すべて一致していれば None。"""
    if counts["relocated"] == 0 and counts["unverified"] == 0:
        return None
    return (
        f"指摘の位置を PDF 上の引用箇所で確かめました：一致 {counts['exact']} 件、"
        f"位置を合わせ直した {counts['relocated']} 件、確認できなかった {counts['unverified']} 件"
    )
