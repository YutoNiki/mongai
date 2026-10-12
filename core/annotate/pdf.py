"""注釈付きPDFの生成（SPEC 4章）。

- 種類は形と色の両方で区別する：論理＝下線、読みやすさ＝網掛け、ルール違反＝波線、
  引用＝枠、研究室知識＝点線の枠
- 指摘の引用箇所（quote）をページ上で探し、見つかればその範囲に、見つからなければ文全体に付ける
- 余白に番号ラベル（「F-0012 論理」）を置く。重なる場合は下にずらす
- ポップアップには「[種類・重大度] 指摘 / 理由 / 根拠 / 改善案」を入れる
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

import pymupdf

from core.analyze.models import Finding
from core.annotate.labels import category_label, severity_label, subcategory_label
from core.annotate.palette import (
    ANNOTATION_COLORS,
    HIGHLIGHT_OPACITY,
    TEXT_BLACK,
    WHITE,
    hex_to_rgb,
)
from core.ingest.models import BBox

# PyMuPDF 組み込みのフォント（外部から取得しない）
LABEL_NUMBER_FONT = "helv"
LABEL_NAME_FONT = "japan"
LABEL_SIZE = 6.5
LABEL_PADDING = 1.5
LABEL_GAP = 1.0
DEFAULT_STATUSES = frozenset({"open"})


@dataclass
class _Labels:
    """ページ・余白（左右）ごとに置いたラベルの位置。重なりを避けるために使う。"""

    placed: dict[tuple[int, str], list[pymupdf.Rect]] = field(default_factory=dict)

    def place(
        self, page_no: int, side: str, rect: pymupdf.Rect, page_height: float
    ) -> pymupdf.Rect:
        taken = self.placed.setdefault((page_no, side), [])
        moved = pymupdf.Rect(rect)
        for other in sorted(taken, key=lambda r: r.y0):
            if moved.intersects(other):
                moved = pymupdf.Rect(
                    moved.x0, other.y1 + LABEL_GAP, moved.x1, other.y1 + LABEL_GAP + rect.height
                )
        if moved.y1 > page_height:  # 下にはみ出す場合は、元の位置の上に積む
            top = min((r.y0 for r in taken), default=rect.y0)
            moved = pymupdf.Rect(rect.x0, top - LABEL_GAP - rect.height, rect.x1, top - LABEL_GAP)
        taken.append(moved)
        return moved


def popup_text(finding: Finding) -> str:
    head = (
        f"[{category_label(finding.category)}・{severity_label(finding.severity)}] "
        f"{subcategory_label(finding.subcategory)}"
    )
    lines = [head, f"指摘：{finding.issue}", f"理由：{finding.rationale}"]
    if finding.evidence:
        lines.append("根拠：")
        lines += [f"・{e.ref}「{e.excerpt}」" for e in finding.evidence]
    if finding.suggestions:
        lines.append("改善案：")
        lines += [f"{i}. {s}" for i, s in enumerate(finding.suggestions, start=1)]
    lines.append(
        f"（MonGAI {finding.finding_id}、{finding.samples} 回中 {finding.votes} 回の判定）"
    )
    return "\n".join(lines)


def _expand(rect: pymupdf.Rect, margin: float) -> pymupdf.Rect:
    return pymupdf.Rect(rect.x0 - margin, rect.y0 - margin, rect.x1 + margin, rect.y1 + margin)


def _rect(box: BBox) -> pymupdf.Rect:
    return pymupdf.Rect(box.x0, box.y0, box.x1, box.y1)


def _target_rects(page: pymupdf.Page, finding: Finding, boxes: list[BBox]) -> list[pymupdf.Rect]:
    """引用箇所をページ上で探す。文の範囲の中で見つからなければ、文の矩形をそのまま使う。"""
    sentence_rects = [_rect(b) for b in boxes]
    clip = pymupdf.Rect(sentence_rects[0])
    for r in sentence_rects[1:]:
        clip |= r
    clip = _expand(clip, 2)
    quote = " ".join(finding.quote.split())
    if quote:
        hits = page.search_for(quote, clip=clip)
        if hits:
            return list(hits)
    return sentence_rects


def _add_shape(page: pymupdf.Page, category: str, rects: list[pymupdf.Rect]) -> list[pymupdf.Annot]:
    color = hex_to_rgb(ANNOTATION_COLORS.get(category, ANNOTATION_COLORS["logic"]))
    annots: list[pymupdf.Annot] = []
    if category in ("logic", "readability", "rule"):
        quads = [r.quad for r in rects]
        if category == "logic":
            annot = page.add_underline_annot(quads)
        elif category == "readability":
            annot = page.add_highlight_annot(quads)
            annot.set_opacity(HIGHLIGHT_OPACITY)
        else:
            annot = page.add_squiggly_annot(quads)
        annot.set_colors(stroke=color)
        annots.append(annot)
    else:
        for r in rects:
            annot = page.add_rect_annot(_expand(r, 1))
            annot.set_colors(stroke=color)
            if category == "lab":
                annot.set_border(width=1.0, dashes=[3, 2])
            else:
                annot.set_border(width=1.0)
            annots.append(annot)
    return annots


def _text_area(page: pymupdf.Page) -> tuple[float, float]:
    """本文の左端と右端（テキストのブロックの外接範囲）。"""
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and str(b[4]).strip()]
    if not blocks:
        return 0.0, page.rect.width
    return min(b[0] for b in blocks), max(b[2] for b in blocks)


def _draw_label(
    page: pymupdf.Page, page_no: int, finding: Finding, anchor: pymupdf.Rect, labels: _Labels
) -> None:
    """余白に「F-0012 論理」のラベルを描く。番号は Helvetica、種類名は日本語フォントで描く。"""
    number = f"{finding.finding_id} "
    name = category_label(finding.category)
    number_width = pymupdf.get_text_length(number, fontname=LABEL_NUMBER_FONT, fontsize=LABEL_SIZE)
    name_width = len(name) * LABEL_SIZE  # 日本語は全角（1文字 = 1em）
    width = number_width + name_width + LABEL_PADDING * 2
    height = LABEL_SIZE + LABEL_PADDING * 2
    page_rect = page.rect
    text_left, text_right = _text_area(page)
    # 本文の外側（余白）に置く。左の段・ページの左半分の指摘は左の余白、それ以外は右の余白
    if (anchor.x0 + anchor.x1) / 2 < page_rect.width / 2:
        side = "left"
        x1 = max(text_left - 2, width + 1)
        rect = pymupdf.Rect(x1 - width, anchor.y0, x1, anchor.y0 + height)
    else:
        side = "right"
        x0 = min(text_right + 2, page_rect.width - width - 1)
        rect = pymupdf.Rect(x0, anchor.y0, x0 + width, anchor.y0 + height)
    rect = labels.place(page_no, side, rect, page_rect.height)
    color = hex_to_rgb(ANNOTATION_COLORS.get(finding.category, ANNOTATION_COLORS["logic"]))
    page.draw_rect(rect, color=color, fill=hex_to_rgb(WHITE), width=0.8, overlay=True)
    baseline = rect.y1 - LABEL_PADDING - 0.8
    text_color = hex_to_rgb(TEXT_BLACK)
    page.insert_text(
        (rect.x0 + LABEL_PADDING, baseline),
        number,
        fontsize=LABEL_SIZE,
        fontname=LABEL_NUMBER_FONT,
        color=text_color,
        overlay=True,
    )
    page.insert_text(
        (rect.x0 + LABEL_PADDING + number_width, baseline),
        name,
        fontsize=LABEL_SIZE,
        fontname=LABEL_NAME_FONT,
        color=text_color,
        overlay=True,
    )


def annotate_pdf(
    pdf: bytes,
    findings: Iterable[Finding],
    *,
    statuses: frozenset[str] = DEFAULT_STATUSES,
) -> bytes:
    """指摘を書き込んだ PDF を返す。statuses に含まれるステータスの指摘だけを書き込む。"""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    labels = _Labels()
    try:
        for finding in findings:
            if finding.status not in statuses:
                continue
            by_page: dict[int, list[BBox]] = {}
            for box in finding.bboxes:
                by_page.setdefault(box.page, []).append(box)
            first = True
            for page_no, boxes in sorted(by_page.items()):
                if not 1 <= page_no <= doc.page_count:
                    continue
                page = doc[page_no - 1]
                rects = _target_rects(page, finding, boxes)
                for annot in _add_shape(page, finding.category, rects):
                    annot.set_info(
                        title=f"MonGAI {finding.finding_id}",
                        subject=subcategory_label(finding.subcategory),
                        content=popup_text(finding),
                    )
                    annot.update()
                if first:  # ラベルは指摘の最初の位置にだけ置く
                    _draw_label(page, page_no, finding, rects[0], labels)
                    first = False
        data: bytes = doc.tobytes(garbage=3, deflate=True)
        return data
    finally:
        doc.close()
