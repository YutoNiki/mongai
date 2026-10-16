"""提出要件のまとめ判定でもID・根拠・分割範囲を取り違えない。"""

import json

import httpx
import pytest
import respx

from core.instructions.check import check_requirements
from core.instructions.models import Requirement, RequirementResult, Snapshot
from core.llm.client import OitomaClient
from tests.unit.doc_factory import make_doc
from tests.unit.test_instructions import instruction_config as llm_config
from tests.unit.test_rules import CHAT_URL, chat


@pytest.mark.parametrize("bad_ids", ["missing", "duplicate", "unexpected"])
async def test_batch_requirements_retries_invalid_id_sets(bad_ids: str) -> None:
    reqs = [Requirement(requirement_id=f"R{i}", text=f"条件{i}") for i in range(3)]
    doc = make_doc([("1 結果", "results", [["具体例は交通計画である。"]])])
    good = [
        {
            "requirement_id": r.requirement_id,
            "verdict": "met",
            "reason": "具体例あり",
            "sentence_ids": ["S-00001"],
            "quote": doc.sentences[0].text,
        }
        for r in reqs
    ]
    bad = (
        good[:2]
        if bad_ids == "missing"
        else [good[0], good[0], good[2]]
        if bad_ids == "duplicate"
        else [good[0], good[1], {**good[2], "requirement_id": "unknown"}]
    )
    with respx.mock:
        route = respx.post(CHAT_URL).mock(
            side_effect=[chat({"results": bad}), chat({"results": good})]
        )
        async with OitomaClient(llm_config(), api_key="k") as client:
            results = await check_requirements(
                client, doc, Snapshot(document_type="course_report", requirements=reqs)
            )
    assert route.call_count == 2
    assert [r.requirement_id for r in results] == [r.requirement_id for r in reqs]
    assert all(r.verdict == "met" and r.coverage == "full" for r in results)


async def test_five_requirements_share_two_manuscript_inputs() -> None:
    reqs = [Requirement(requirement_id=f"R{i}", text=f"条件{i}") for i in range(5)]
    doc = make_doc([("1 結果", "results", [["結果である。"]])])

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        item = {"verdict": "unknown", "reason": "根拠不足"}
        return (
            chat(
                {
                    "results": [
                        {**item, "requirement_id": r["requirement_id"]}
                        for r in payload["requirements"]
                    ]
                }
            )
            if "requirements" in payload
            else chat(item)
        )

    with respx.mock:
        route = respx.post(CHAT_URL).mock(side_effect=respond)
        async with OitomaClient(llm_config(), api_key="k") as client:
            results = await check_requirements(
                client, doc, Snapshot(document_type="course_report", requirements=reqs)
            )
    assert route.call_count == 2 and len(results) == 5


async def test_batch_keeps_each_requirements_evidence_and_progress_separate() -> None:
    doc = make_doc([("1 本論", "other", [["具体例は交通計画である。"]])])
    reqs = [Requirement(requirement_id=f"R{i}", text=f"条件{i}") for i in range(3)]
    payload = {
        "results": [
            {
                "requirement_id": req.requirement_id,
                "verdict": "met",
                "reason": "具体例",
                "sentence_ids": ["S-00001"],
                "quote": "具体例は交通計画である。" if i != 1 else "原稿にない根拠",
            }
            for i, req in enumerate(reqs)
        ]
    }
    progress: list[tuple[str, int, int]] = []

    async def report(item: RequirementResult, done: int, total: int) -> None:
        progress.append((item.requirement_id, done, total))

    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=chat(payload))
        async with OitomaClient(llm_config(), api_key="k") as client:
            results = await check_requirements(
                client,
                doc,
                Snapshot(document_type="course_report", requirements=reqs),
                on_result=report,
            )
    assert [r.verdict for r in results] == ["met", "unknown", "met"]
    assert not results[1].quote and not results[1].sentence_ids
    assert progress == [(f"R{i}", i + 1, 3) for i in range(3)]


async def test_split_requirement_checks_keep_source_ids_and_aggregate_all_chunks() -> None:
    doc = make_doc([("1 本論", "other", [["合成された本文である。" * 350]])])
    reqs = [
        Requirement(requirement_id=f"R{i}", text="本文を確認する", aggregation="all")
        for i in range(2)
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        data = json.loads(json.loads(request.content)["messages"][-1]["content"])
        assert not data["full_document"]
        assert "【S-00001】" in data["manuscript"]
        return chat(
            {
                "results": [
                    {
                        "requirement_id": r["requirement_id"],
                        "verdict": "met",
                        "reason": "本文あり",
                        "sentence_ids": ["S-00001"],
                        "quote": "合成された本文である。",
                    }
                    for r in data["requirements"]
                ]
            }
        )

    with respx.mock:
        route = respx.post(CHAT_URL).mock(side_effect=respond)
        async with OitomaClient(
            llm_config(small=True).model_copy(update={"context_window": 4096}), api_key="k"
        ) as client:
            results = await check_requirements(
                client, doc, Snapshot(document_type="course_report", requirements=reqs)
            )
    assert route.call_count > 1
    assert [r.verdict for r in results] == ["met", "met"]
