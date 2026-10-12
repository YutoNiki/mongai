"""指摘の位置を PDF 上の引用箇所に合わせる処理。"""

from pathlib import Path

import pymupdf

from core.annotate.locate import relocate_findings, summary
from core.ingest.models import BBox
from tests.unit.pdf_factory import lines, make_pdf
from tests.unit.test_annotate import finding

PAGE1 = [
    "The proposed method clearly outperforms all existing methods on every task.",
    "This makes the approach suitable for summarization and translation alike.",
]
PAGE2 = [
    "We evaluate the approach on two datasets for headline generation and",
    "document summarization, which are widely used in prior studies today.",
]


def build(tmp_path: Path) -> tuple[bytes, dict[str, BBox]]:
    path = make_pdf(tmp_path / "p.pdf", [lines(72, 120, PAGE1), lines(72, 120, PAGE2)])
    boxes: dict[str, BBox] = {}
    with pymupdf.open(path) as doc:
        for page_no, texts in ((1, PAGE1), (2, PAGE2)):
            for t in texts:
                r = doc[page_no - 1].search_for(t)[0]
                boxes[t] = BBox(page=page_no, x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1)
    return path.read_bytes(), boxes


def test_exact_and_narrowed(tmp_path: Path) -> None:
    pdf, boxes = build(tmp_path)
    f = finding("F-0001", "logic", boxes[PAGE1[0]], "clearly outperforms")
    [out], counts = relocate_findings(pdf, [f])
    assert counts == {"exact": 1}
    assert out.page == 1 and len(out.bboxes) == 1
    assert out.bboxes[0].x1 - out.bboxes[0].x0 < (boxes[PAGE1[0]].x1 - boxes[PAGE1[0]].x0) / 2


def test_wrong_line_and_page_are_relocated(tmp_path: Path) -> None:
    pdf, boxes = build(tmp_path)
    # 座標は1ページ目の1行目を指しているが、引用箇所は2ページ目にある
    f = finding("F-0001", "logic", boxes[PAGE1[0]], "two datasets for headline generation")
    [out], counts = relocate_findings(pdf, [f])
    assert counts == {"relocated": 1}
    assert out.page == 2 and out.bboxes[0].page == 2
    assert abs(out.bboxes[0].y0 - boxes[PAGE2[0]].y0) < 2


def test_quote_spanning_lines(tmp_path: Path) -> None:
    pdf, boxes = build(tmp_path)
    quote = "headline generation and document summarization, which are widely used"
    f = finding("F-0001", "logic", boxes[PAGE1[1]], quote)
    [out], counts = relocate_findings(pdf, [f])
    assert counts == {"relocated": 1}
    assert {round(b.y0) for b in out.bboxes} == {
        round(boxes[PAGE2[0]].y0),
        round(boxes[PAGE2[1]].y0),
    }


def test_unverified_keeps_original(tmp_path: Path) -> None:
    pdf, boxes = build(tmp_path)
    f = finding("F-0001", "logic", boxes[PAGE1[0]], "この文は PDF のどこにも無い")
    [out], counts = relocate_findings(pdf, [f])
    assert counts == {"unverified": 1}
    assert out.bboxes == f.bboxes
    assert summary(counts) is not None and "確認できなかった 1 件" in (summary(counts) or "")
    assert (
        summary(relocate_findings(pdf, [finding("F-2", "logic", boxes[PAGE1[0]], PAGE1[0])])[1])
        is None
    )


def test_text_is_found_regardless_of_box_page(tmp_path: Path) -> None:
    pdf, boxes = build(tmp_path)
    f = finding("F-0001", "logic", boxes[PAGE2[1]], PAGE1[1])
    [out], _ = relocate_findings(pdf, [f])
    assert out.page == 1
