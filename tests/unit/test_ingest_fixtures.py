"""公開OA論文（CC BY 4.0、tests/fixtures/README.md）での構造化の確認。GROBID は使わない。

GROBID を使う確認は tests/integration/test_ingest_grobid.py（make test-integration）で行う。
"""

from pathlib import Path

import pytest

from core.ingest.models import Document
from core.ingest.pipeline import ingest_pdf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EN = FIXTURES / "en_extraphrase_naacl2022.pdf"
JA = FIXTURES / "ja_extraphrase_jnlp2023.pdf"


@pytest.fixture(scope="module")
async def en_doc() -> Document:
    return await ingest_pdf(EN, use_grobid=False)


@pytest.fixture(scope="module")
async def ja_doc() -> Document:
    return await ingest_pdf(JA, use_grobid=False)


def headings(doc: Document) -> list[str]:
    return [f"{s.number} {s.title}" if s.number else s.title for s in doc.sections[1:]]


async def test_en_sections(en_doc: Document) -> None:
    assert en_doc.lang == "en"
    assert en_doc.page_count == 9
    assert headings(en_doc) == [
        "Abstract",
        "1 Introduction",
        "2 Proposed Method: ExtraPhrase",
        "2.1 Step-1: Extractive Summarization",
        "2.2 Step-2: Paraphrasing",
        "3 Experiments",
        "3.1 Datasets",
        "3.2 Comparison Methods",
        "3.3 Encoder-Decoder Architecture",
        "3.4 Results",
        "4 Analysis",
        "4.1 Low-resource Setting",
        "4.2 Diversity of Pseudo Summaries",
        "5 Efficiency of Pseudo-data Generation",
        "6 Related Work",
        "7 Conclusion",
        "Acknowledgements",
        "References",
        "A Quality of Back-translation",
    ]
    roles = {s.title: s.role for s in en_doc.sections}
    assert roles["Introduction"] == "introduction"
    assert roles["Comparison Methods"] == "experiments"
    assert roles["Results"] == "results"
    assert roles["References"] == "references"


async def test_ja_sections(ja_doc: Document) -> None:
    assert ja_doc.lang == "ja"
    assert ja_doc.page_count == 18
    assert headings(ja_doc)[:4] == [
        "1 はじめに",
        "2 提案手法: ExtraPhrase",
        "2.1 文圧縮",
        "2.2 言い換え",
    ]
    assert "参考文献" in headings(ja_doc)
    assert ja_doc.sections[0].role == "abstract"  # 見出しの無い冒頭の要旨


async def test_known_sentences(en_doc: Document, ja_doc: Document) -> None:
    en = {s.text for s in en_doc.sentences}
    assert (
        "In sequence-to-sequence tasks, we need a parallel corpus to train neural encoder-decoders."
        in en
    )
    # 段・ページの区切りと図をまたいで続く文
    assert any(t.endswith("to the extracted text to obtain diverse pseudo summaries.") for t in en)
    ja = {s.text for s in ja_doc.sentences}
    assert "本研究では見出し生成タスクと文書要約タスクで実験を行う．" in ja


@pytest.mark.parametrize("name", ["en_doc", "ja_doc"])
async def test_sentence_boxes_are_on_page(name: str, request: pytest.FixtureRequest) -> None:
    doc: Document = request.getfixturevalue(name)
    assert len(doc.sentences) > 200
    for s in doc.sentences:
        assert s.bboxes, s.sentence_id
        assert s.page == s.bboxes[0].page
        for b in s.bboxes:
            assert 1 <= b.page <= doc.page_count
            assert 0 <= b.x0 < b.x1 <= 600 and 0 <= b.y0 < b.y1 <= 850


@pytest.mark.parametrize("name", ["en_doc", "ja_doc"])
async def test_citations_resolved_without_grobid(name: str, request: pytest.FixtureRequest) -> None:
    doc: Document = request.getfixturevalue(name)
    assert len(doc.citations) >= 39
    assert all(c.resolved for c in doc.citations), [
        c.marker for c in doc.citations if not c.resolved
    ]
    sentence_ids = {s.sentence_id for s in doc.sentences}
    ref_ids = {r.ref_id for r in doc.references}
    for c in doc.citations:
        assert c.sentence_id in sentence_ids
        assert set(c.ref_ids) <= ref_ids


async def test_hidden_clipped_text_is_ignored(ja_doc: Document) -> None:
    # 3ページ目の図の中に、表示されない本文の複製が埋め込まれている。それを文として取り出さない
    hits = [s for s in ja_doc.sentences if "生成された疑似データの性質を分析し" in s.text]
    assert len(hits) == 1
    assert hits[0].text.endswith("示した（4.4 節）．")
