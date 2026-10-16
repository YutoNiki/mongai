"""モデルの応答を誤った原文や正常な空結果として確定させない。"""

import asyncio
from typing import Any

import httpx
import pytest
import respx

from core.analyze.citation import check_citations
from core.analyze.context import select_targets
from core.analyze.models import ParagraphIssue
from core.analyze.paragraph_check import check_paragraph, same_issue
from core.analyze.suggest import keeps_terms, suggest
from core.analyze.voting import majority
from core.ingest.models import Citation
from core.llm.client import JSONOutputError, OitomaClient
from tests.unit.doc_factory import make_doc
from tests.unit.test_analyze_pipeline import CHAT_URL, config, reply
from tests.unit.test_citation import FakeGateway, llm_config, make_paper
from tests.unit.test_store import finding


@pytest.mark.parametrize(
    ("original", "rewrite"),
    [
        ("参加者は10人である。", "参加者は100人であった。"),
        ("変化量は-5%である。", "変化量は5%であった。"),
        ("投与量は10 mgである。", "投与量は10 kgであった。"),
        ("精度は95%である。", "精度は195%であった。"),
    ],
)
def test_rewrite_preserves_values_signs_and_units(original: str, rewrite: str) -> None:
    assert not keeps_terms(original, rewrite)


async def test_rewrite_does_not_invent_numbers_outside_short_quote() -> None:
    text = "参加者は10人で、結果は有効だった。"
    doc = make_doc([("4 結果", "results", [[text]])])
    item = finding("F-1", quote="有効だった", sentence_ids=["S-00001"])
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=reply({"suggestions": ["参加者は100人で、結果は有効だった。"]})
        )
        async with OitomaClient(config(), api_key="synthetic") as client:
            assert await suggest(client, doc, item, prompt_lang="ja") == []


async def _paragraph(payloads: list[httpx.Response]) -> Any:
    doc = make_doc(
        [
            (
                "1 はじめに",
                "introduction",
                [
                    [
                        "評価した条件では改善が見られたという結果について、比較の方法と範囲を説明する合成文章である。"
                    ]
                ],
            )
        ]
    )
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=payloads)
        async with OitomaClient(config(), api_key="synthetic") as client:
            return await check_paragraph(
                client,
                select_targets(doc)[0],
                None,
                prompt_lang="ja",
                output_language="日本語",
                known_sentences={"S-00001"},
                context_text="",
            )


async def test_missing_issues_key_is_not_a_clean_result() -> None:
    with pytest.raises(JSONOutputError):
        await _paragraph([reply({"error": "cannot evaluate"}) for _ in range(9)])


async def test_failed_majority_is_unconfirmed() -> None:
    with pytest.raises(JSONOutputError, match="未確認"):
        await _paragraph([reply({"issues": []}), httpx.Response(503), httpx.Response(503)])


async def test_fabricated_quote_is_not_substituted_for_real_text() -> None:
    issue = {
        "type": "overclaim",
        "sentence_ids": ["S-00001"],
        "quote": "あらゆる条件で必ず100%成功する",
        "issue": "100%の成功という主張は過大である。",
        "rationale": "根拠がない。",
        "severity": "major",
    }
    voted, _ = await _paragraph(
        [reply({"issues": [issue]}), *[reply({"issues": []}) for _ in range(3)]]
    )
    assert voted == []


def test_distinct_claims_do_not_form_a_majority() -> None:
    def item(quote: str) -> ParagraphIssue:
        return ParagraphIssue(
            type="overclaim",
            sentence_ids=["S-1"],
            quote=quote,
            issue=quote,
            rationale="根拠がない。",
            severity="major",
        )

    assert majority([[item("精度が高い")], [item("処理が速い")], []], same=same_issue, n=3) == []


def test_majority_does_not_merge_through_a_bridge() -> None:
    assert (
        majority([[{"a"}], [{"a", "b"}], [{"b"}]], same=lambda a, b: bool(a & b), n=3)[0].votes == 2
    )


class SlowGateway(FakeGateway):
    async def submit(self, analysis_id: str, doc_id: str, request: Any) -> Any:
        if request.kind == "oa_fetch":
            await asyncio.sleep(0.01)
        return await super().submit(analysis_id, doc_id, request)


async def test_every_citation_waits_for_shared_abstract() -> None:
    doc, structures = make_paper()
    second = Citation(
        citation_id="C-2",
        sentence_id="S-00002",
        marker="[1]",
        style="numeric",
        ref_ids=["R-001"],
        resolved=True,
    )
    doc = doc.model_copy(update={"citations": [*doc.citations, second]})
    gateway = SlowGateway("This paper studies image classification.")
    with respx.mock:
        endpoint = respx.post(CHAT_URL).mock(
            return_value=reply(
                {
                    "verdict": "does_not_support",
                    "reason": "The topic differs.",
                    "evidence": "image classification",
                }
            )
        )
        async with OitomaClient(llm_config(), api_key="synthetic") as client:
            result = await check_citations(
                doc,
                structures,
                client,
                gateway,
                "A-test",
                prompt_lang="ja",
                output_lang="ja",
                output_language="日本語",
            )
        assert endpoint.call_count == 2
    assert sum(d.subcategory == "citation_unsupported" for d in result.drafts) == 2
    assert sum(r["kind"] == "oa_fetch" for r in gateway.requests) == 1


async def test_external_evidence_must_exist_in_source() -> None:
    doc, structures = make_paper()
    with respx.mock:
        endpoint = respx.post(CHAT_URL).mock(
            return_value=reply(
                {
                    "verdict": "does_not_support",
                    "reason": "The result contradicts it.",
                    "evidence": "Accuracy decreased by 40%.",
                }
            )
        )
        async with OitomaClient(llm_config(), api_key="synthetic") as client:
            result = await check_citations(
                doc,
                structures,
                client,
                FakeGateway("This paper studies image classification."),
                "A-test",
                prompt_lang="ja",
                output_lang="ja",
                output_language="日本語",
            )
        assert endpoint.call_count == 3
    assert all(d.subcategory != "citation_unsupported" for d in result.drafts)
    assert any("判定に失敗" in warning for warning in result.warnings)
