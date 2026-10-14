"""注釈付きPDFの生成（SPEC 4章）と、注釈の色のコントラスト。"""

from pathlib import Path

import pymupdf
import pytest

from core.analyze.models import Evidence, Finding, ModelInfo
from core.annotate.palette import (
    ANNOTATION_COLORS,
    HIGHLIGHT_OPACITY,
    contrast_ratio,
    hex_to_rgb,
)
from core.annotate.pdf import annotate_pdf, popup_text
from core.ingest.models import BBox
from tests.unit.pdf_factory import Text, make_pdf

LINES = [
    "The proposed method clearly outperforms all existing methods on every task.",
    "This makes the approach suitable for summarization and translation alike.",
    "We follow the formatting rules of the venue for all tables and figures here.",
    "Prior work reported similar gains on two datasets in the same setting [3].",
    "Our lab previously received a comment about the size of the evaluation set.",
]
SHAPES = {
    "logic": "Underline",
    "readability": "Highlight",
    "rule": "Squiggly",
    "citation": "Square",
    "lab": "Square",
}


@pytest.fixture
def pdf_and_boxes(tmp_path: Path) -> tuple[bytes, list[BBox]]:
    path = make_pdf(tmp_path / "p.pdf", [[Text(72, 120 + i * 20, t) for i, t in enumerate(LINES)]])
    data = path.read_bytes()
    with pymupdf.open(path) as doc:
        boxes = []
        for text in LINES:
            r = doc[0].search_for(text)[0]
            boxes.append(BBox(page=1, x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1))
    return data, boxes


def read_annots(pdf: bytes, page_no: int = 1) -> list[dict[str, object]]:
    """注釈の属性を辞書に写して返す（ページを閉じた後に注釈を触ると PyMuPDF が落ちるため）。"""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[page_no - 1]
        return [
            {
                "type": a.type[1],
                "rect": pymupdf.Rect(a.rect),
                "stroke": a.colors["stroke"],
                "opacity": a.opacity,
                "dashes": a.border.get("dashes"),
                "title": a.info["title"],
                "content": a.info["content"],
            }
            for a in page.annots()
        ]


def page_text(pdf: bytes, page_no: int = 1) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return str(doc[page_no - 1].get_text())


def search(pdf: bytes, needle: str, page_no: int = 1) -> list[pymupdf.Rect]:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return [pymupdf.Rect(r) for r in doc[page_no - 1].search_for(needle)]


def finding(fid: str, category: str, box: BBox, quote: str, **kw: object) -> Finding:
    base: dict[str, object] = dict(
        finding_id=fid,
        doc_id="d",
        category=category,
        subcategory="overclaim",
        severity="major",
        page=box.page,
        bboxes=[box],
        sentence_ids=["S-00001"],
        quote=quote,
        issue="根拠に比べて主張が強い",
        rationale="評価は二つのデータセットだけである",
        confidence="low",
        votes=2,
        samples=3,
        models=ModelInfo(primary="m"),
        suggestions=["案1", "案2"],
        evidence=[Evidence(type="manuscript", ref="S-00004", excerpt="two datasets")],
    )
    base.update(kw)
    return Finding.model_validate(base)


@pytest.mark.parametrize("category", sorted(ANNOTATION_COLORS))
def test_annotation_colors_have_3_to_1_contrast_on_white(category: str) -> None:
    assert contrast_ratio(ANNOTATION_COLORS[category]) >= 3.0


def test_contrast_ratio_reference_values() -> None:
    assert contrast_ratio("#000000") == pytest.approx(21.0)
    assert contrast_ratio("#FFFFFF") == pytest.approx(1.0)


def test_each_category_has_distinct_shape(pdf_and_boxes: tuple[bytes, list[BBox]]) -> None:
    data, boxes = pdf_and_boxes
    categories = ["logic", "readability", "rule", "citation", "lab"]
    findings = [finding(f"F-000{i + 1}", c, boxes[i], LINES[i]) for i, c in enumerate(categories)]
    out = annotate_pdf(data, findings)
    annots = {a["title"]: a for a in read_annots(out)}
    assert set(annots) == {f"SI-NSA F-000{i}" for i in range(1, 6)}
    for i, category in enumerate(categories, start=1):
        annot = annots[f"SI-NSA F-000{i}"]
        assert annot["type"] == SHAPES[category]
        assert annot["stroke"] == pytest.approx(hex_to_rgb(ANNOTATION_COLORS[category]), abs=0.01)
    assert annots["SI-NSA F-0002"]["opacity"] == pytest.approx(HIGHLIGHT_OPACITY, abs=0.01)
    assert annots["SI-NSA F-0005"]["dashes"]  # 専門知識は点線の枠
    assert not annots["SI-NSA F-0004"]["dashes"]  # 引用は実線の枠
    # 余白のラベルに、種類名が文字で入っている（色だけで区別しない）
    text = page_text(out)
    for label in [
        "F-0001 論理",
        "F-0002 読みやすさ",
        "F-0003 ルール違反",
        "F-0004 引用",
        "F-0005 専門知識",
    ]:
        assert label in text


def test_popup_contents(pdf_and_boxes: tuple[bytes, list[BBox]]) -> None:
    data, boxes = pdf_and_boxes
    f = finding("F-0012", "logic", boxes[0], LINES[0], severity="critical")
    text = popup_text(f)
    assert text.splitlines()[0] == "[論理・重大] 主張の過大"
    assert "指摘：根拠に比べて主張が強い" in text
    assert "根拠：" in text and "S-00004「two datasets」" in text
    assert "改善案：\n1. 案1\n2. 案2" in text
    assert read_annots(annotate_pdf(data, [f]))[0]["content"] == text


def test_popup_without_votes_for_single_judgement(pdf_and_boxes: tuple[bytes, list[BBox]]) -> None:
    _, boxes = pdf_and_boxes
    voted = popup_text(finding("F-0001", "logic", boxes[0], LINES[0]))
    single = popup_text(finding("F-0002", "citation", boxes[0], LINES[0], votes=1, samples=1))
    assert "3 回中" in voted
    assert single.endswith("（SI-NSA F-0002）") and "回中" not in single


def test_annotation_narrowed_to_quote(pdf_and_boxes: tuple[bytes, list[BBox]]) -> None:
    data, boxes = pdf_and_boxes
    out = annotate_pdf(data, [finding("F-0001", "logic", boxes[0], "clearly outperforms")])
    rect = read_annots(out)[0]["rect"]
    assert isinstance(rect, pymupdf.Rect)
    assert rect.width < (boxes[0].x1 - boxes[0].x0) / 2
    assert rect.x0 == pytest.approx(search(out, "clearly outperforms")[0].x0, abs=1.5)


def test_falls_back_to_sentence_when_quote_not_found(
    pdf_and_boxes: tuple[bytes, list[BBox]],
) -> None:
    data, boxes = pdf_and_boxes
    out = annotate_pdf(data, [finding("F-0001", "logic", boxes[0], "言い換えた引用")])
    rect = read_annots(out)[0]["rect"]
    assert isinstance(rect, pymupdf.Rect)
    assert rect.width == pytest.approx(boxes[0].x1 - boxes[0].x0, abs=2)


def test_dismissed_findings_are_not_written(pdf_and_boxes: tuple[bytes, list[BBox]]) -> None:
    data, boxes = pdf_and_boxes
    findings = [
        finding("F-0001", "logic", boxes[0], LINES[0]),
        finding(
            "F-0002", "logic", boxes[1], LINES[1], status="dismissed", dismiss_reason="意図どおり"
        ),
        finding("F-0003", "logic", boxes[2], LINES[2], status="accepted"),
    ]
    assert [a["title"] for a in read_annots(annotate_pdf(data, findings))] == ["SI-NSA F-0001"]
    both = annotate_pdf(data, findings, statuses=frozenset({"open", "accepted"}))
    assert len(read_annots(both)) == 2


def test_labels_do_not_overlap(pdf_and_boxes: tuple[bytes, list[BBox]]) -> None:
    data, boxes = pdf_and_boxes
    # 同じ行に3件の指摘があってもラベルが重ならない
    findings = [finding(f"F-000{i}", "logic", boxes[0], LINES[0]) for i in range(1, 4)]
    out = annotate_pdf(data, findings)
    rects = [search(out, f"F-000{i} 論理")[0] for i in range(1, 4)]
    for a in range(3):
        for b in range(a + 1, 3):
            assert not rects[a].intersects(rects[b])
    assert all(r.x1 <= boxes[0].x0 for r in rects)  # 左の余白に置く


def test_multi_page_finding(tmp_path: Path) -> None:
    pages = [
        [Text(72, 700, "A sentence that starts at the bottom of page one and")],
        [Text(72, 100, "continues at the top of page two.")],
    ]
    data = make_pdf(tmp_path / "m.pdf", pages).read_bytes()
    boxes = [
        BBox(page=1, x0=72, y0=690, x1=400, y1=702),
        BBox(page=2, x0=72, y0=90, x1=250, y1=102),
    ]
    out = annotate_pdf(data, [finding("F-0001", "rule", boxes[0], "x", bboxes=boxes)])
    assert len(read_annots(out, 1)) == 1 and len(read_annots(out, 2)) == 1
    assert "F-0001" in page_text(out, 1) and "F-0001" not in page_text(out, 2)
