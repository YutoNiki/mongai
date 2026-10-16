"""図表参照・用語定義と前後文脈を合成原稿で検証する。"""

import pytest
import respx

from core.analyze.context import select_targets
from core.analyze.context_review import review_context, review_violations
from core.analyze.terms import lookup_terms
from core.llm.client import JSONOutputError, OitomaClient
from core.rules.mechanical import check_abbreviations, check_figure_references
from tests.unit.doc_factory import make_doc
from tests.unit.test_analyze_pipeline import S1, S2
from tests.unit.test_rules import BUILTIN, CHAT_URL, chat, config
from tests.unit.test_terms import QueueingGateway


def test_short_paragraphs_stay_in_context() -> None:
    doc = make_doc([("1 手法", "methods", [[S1], ["LSTMとは記憶を持つモデルである。"], [S2]])])
    targets = select_targets(doc)
    assert len(targets) == 2
    assert targets[0].next == doc.paragraphs[1]
    assert targets[1].previous == doc.paragraphs[1]
    assert select_targets(doc, max_paragraphs=1)[0].next == doc.paragraphs[1]


@pytest.mark.parametrize(
    "term,definition",
    [
        ("LSTM", "LSTMとは長期の記憶を保持する再帰型モデルである。"),
        ("LSTM", "長短期記憶を以下LSTMと呼ぶ。"),
    ],
)
async def test_prose_definition_resolves_abbreviation(term: str, definition: str) -> None:
    doc = make_doc([("1 手法", "methods", [[definition], [f"{term}を用いる。{term}で評価する。"]])])
    candidates = check_abbreviations(doc, BUILTIN["abbreviation_definition"], "ja")
    assert candidates
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=chat(
                {
                    "results": [
                        {
                            "candidate_id": "0",
                            "status": "resolved",
                            "sentence_ids": ["S-00001"],
                            "quote": definition,
                        }
                    ]
                }
            )
        )
        async with OitomaClient(config(), api_key="k") as client:
            kept, warnings = await review_violations(client, doc, candidates, lang="ja")
    assert not kept and not warnings


@pytest.mark.parametrize("status", ["resolved", "unknown", "valid"])
async def test_implicit_table_reference(status: str) -> None:
    doc = make_doc([("1 結果", "results", [["以下の表より精度が改善した。"], ["表1 精度比較"]])])
    doc.paragraphs[1].kind = "caption"
    candidates = check_figure_references(doc, BUILTIN["figure_table_reference"], "ja")
    assert len(candidates) == 1
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=chat(
                {
                    "results": [
                        {
                            "candidate_id": "0",
                            "status": status,
                            "sentence_ids": ["S-00001"] if status == "resolved" else [],
                            "quote": doc.sentences[0].text if status == "resolved" else "",
                        }
                    ]
                }
            )
        )
        async with OitomaClient(config(), api_key="k") as client:
            kept, warnings = await review_violations(client, doc, candidates, lang="ja")
    assert bool(kept) == (status == "valid")
    assert bool(warnings) == (status == "unknown")
    if kept:
        assert kept[0].model != "mechanical"


async def test_definition_elsewhere_prevents_external_lookup() -> None:
    definition = "アイソクローンとは一定時間で到達できる範囲を表す線である。"
    doc = make_doc(
        [
            ("1 背景", "introduction", [[definition]]),
            ("2 手法", "methods", [["アイソクローンを使う。"]]),
        ]
    )
    gateway = QueueingGateway()
    with respx.mock:
        respx.post(CHAT_URL).mock(
            side_effect=[
                chat({"terms": ["アイソクローン"]}),
                chat({"terms": []}),
                chat(
                    {
                        "results": [
                            {
                                "candidate_id": "アイソクローン",
                                "status": "resolved",
                                "sentence_ids": ["S-00001"],
                                "quote": definition,
                            }
                        ]
                    }
                ),
            ]
        )
        async with OitomaClient(config(), api_key="k") as client:
            result = await lookup_terms(
                client, gateway, doc, select_targets(doc, min_chars=1), analysis_id="A", lang="ja"
            )
    assert not gateway.requests and not result.warnings


async def test_context_evidence_must_be_real() -> None:
    doc = make_doc([("1 手法", "methods", [["LSTMを使う。"]])])
    with respx.mock:
        route = respx.post(CHAT_URL).mock(
            return_value=chat(
                {
                    "results": [
                        {
                            "candidate_id": "x",
                            "status": "resolved",
                            "sentence_ids": ["S-00001"],
                            "quote": "LSTMとは長短期記憶である。",
                        }
                    ]
                }
            )
        )
        async with OitomaClient(config(), api_key="k") as client:
            with pytest.raises(JSONOutputError):
                await review_context(
                    client, doc, [{"candidate_id": "x", "kind": "term", "term": "LSTM"}], lang="ja"
                )
        assert route.call_count == 3


async def test_late_prose_definition_is_not_reported_as_undefined() -> None:
    definition = "LSTMとは長期の記憶を保持するモデルである。"
    doc = make_doc([("1 手法", "methods", [["LSTMを使う。LSTMを評価する。"], [definition]])])
    candidates = check_abbreviations(doc, BUILTIN["abbreviation_definition"], "ja")
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=chat(
                {
                    "results": [
                        {
                            "candidate_id": "0",
                            "status": "late_definition",
                            "sentence_ids": ["S-00002"],
                            "quote": definition,
                        }
                    ]
                }
            )
        )
        async with OitomaClient(config(), api_key="k") as client:
            kept, warnings = await review_violations(client, doc, candidates, lang="ja")
    assert len(kept) == 1 and not warnings
    assert "定義より前" in kept[0].issue
    assert "定義されていません" not in kept[0].issue
