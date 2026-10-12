"""PyMuPDF による行・段落の復元（合成PDF）。"""

from pathlib import Path

from core.ingest.layout import analyze_layout, char_boxes, split_hanging_entries
from tests.unit.pdf_factory import Text, lines, make_pdf

# 両端揃えの本文を再現するため、等幅フォント（Courier）で同じ文字数の行にする
BODY_EN = [
    "Neural encoder-decoders achieve remarkable results in sequence-",
    "to-sequence tasks such as translation and summarization. They ",
    "require a large amount of parallel data, which is costly to do.",
    "A short last line ends it.",
]


def running_heads(page_no: int) -> list[Text]:
    return [
        Text(72, 40, "Journal of Synthetic Studies Vol. 1", size=8),
        Text(290, 815, str(page_no), size=8),
    ]


def test_english_paragraphs_headings_and_running_heads(tmp_path: Path) -> None:
    page1 = [
        *running_heads(1),
        Text(72, 100, "1 Introduction", font="hebo", size=12),
        *lines(72, 125, BODY_EN, font="cour"),
        *lines(
            72,
            190,
            [
                "   A second paragraph starts with an indented first line, and",
                "it continues on the next line.",
            ],
            font="cour",
        ),
    ]
    page2 = [
        *running_heads(2),
        Text(72, 100, "2 Method", font="hebo", size=12),
        *lines(72, 125, ["We describe the proposed method in this section of the paper."]),
    ]
    page3 = [
        *running_heads(3),
        *lines(72, 100, ["The third page has text too, to repeat the running head."]),
    ]
    layout = analyze_layout(make_pdf(tmp_path / "en.pdf", [page1, page2, page3]))

    texts = [(p.kind, p.text) for p in layout.paragraphs]
    assert ("heading", "1 Introduction") in texts
    assert ("heading", "2 Method") in texts
    assert not any("Journal of Synthetic" in t for _, t in texts)
    assert not any(t.strip() in {"1", "2", "3"} for _, t in texts)

    body = [p for p in layout.paragraphs if p.kind == "body"]
    first = body[0].text
    # 行末のハイフンを取り除き（"sequence-" + "to-sequence"）、英語の行の間には空白を補う
    assert "sequenceto-sequence" in first
    assert "translation and summarization. They require" in first
    assert first.endswith("A short last line ends it.")
    assert body[1].text.startswith("A second paragraph")


def test_japanese_lines_join_without_spaces(tmp_path: Path) -> None:
    page = [
        Text(72, 100, "1 はじめに", font="japan", size=12),
        *lines(
            72,
            125,
            [
                "本研究では，論文の投稿前に読みにくさを検出する手法を提案す",
                "る．提案手法は前提と根拠のずれを検出する．",
            ],
            font="japan",
            indent_first=10,
        ),
    ]
    layout = analyze_layout(make_pdf(tmp_path / "ja.pdf", [page]))
    kinds = [(p.kind, p.text) for p in layout.paragraphs]
    assert kinds[0] == ("heading", "1 はじめに")
    assert kinds[1] == (
        "body",
        "本研究では，論文の投稿前に読みにくさを検出する手法を提案する．提案手法は前提と根拠のずれを検出する．",
    )


def test_char_boxes_per_line(tmp_path: Path) -> None:
    page = lines(
        72, 100, ["First line of a sentence that", "continues on the second line."], indent_first=0
    )
    layout = analyze_layout(make_pdf(tmp_path / "b.pdf", [page]))
    para = layout.paragraphs[0]
    boxes = char_boxes(para.chars)
    assert len(boxes) == 2
    assert all(b.page == 1 and 60 < b.x0 < b.x1 < 595 and 80 < b.y0 < b.y1 < 130 for b in boxes)
    assert boxes[0].y1 <= boxes[1].y1


def test_hanging_indent_references(tmp_path: Path) -> None:
    page = [
        Text(72, 100, "References", font="hebo", size=12),
        Text(72, 125, "Bahdanau, D., Cho, K., and Bengio, Y. (2015). Neural machine translation"),
        Text(84, 138, "by jointly learning to align and translate. In ICLR."),
        Text(72, 151, "Bolshakov, I. A. and Gelbukh, A. (2004). Synonymous paraphrasing using"),
        Text(84, 164, "WordNet and Internet. In NLDB, pp. 312-323."),
    ]
    layout = analyze_layout(make_pdf(tmp_path / "r.pdf", [page]))
    refs = [p for p in layout.paragraphs if p.kind != "heading"]
    entries = split_hanging_entries(refs)
    assert entries == [
        "Bahdanau, D., Cho, K., and Bengio, Y. (2015). Neural machine translation by jointly learning to align and translate. In ICLR.",
        "Bolshakov, I. A. and Gelbukh, A. (2004). Synonymous paraphrasing using WordNet and Internet. In NLDB, pp. 312-323.",
    ]


def test_paragraph_continues_across_columns_and_figures(tmp_path: Path) -> None:
    # 左の段の最後で文が途切れ、図中の文字（小さい字）を挟んで右の段の先頭に続く
    left = lines(
        60,
        700,
        [
            "Then we apply a paraphrasing method to the",
            "extracted text in order to obtain diverse",
        ],
        font="cour",
    )
    figure = [Text(330, 80, "Figure text", size=7), Text(330, 90, "more figure text", size=7)]
    right = [
        Text(310, 120, "pseudo summaries for the training data.", font="cour"),
        Text(310, 160, "Back-translation 3.8M (1K) 38.51 19.52", font="cour"),
    ]
    layout = analyze_layout(make_pdf(tmp_path / "c.pdf", [[*left, *figure, *right]]))
    body = [p.text for p in layout.paragraphs if p.kind == "body"]
    assert (
        "Then we apply a paraphrasing method to the extracted text in order to obtain diverse "
        "pseudo summaries for the training data."
    ) in body
    # 大文字で始まる表の行はつながない
    assert not any("diverse Back-translation" in t for t in body)
