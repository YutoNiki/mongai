"""遅い構造抽出を待たず、別の段落の改善案まで進める。"""

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx

from core.analyze.context import Target
from core.analyze.models import ParagraphStructure
from core.analyze.pipeline import AnalysisSettings, analyze_logic
from core.analyze.structure import extract_structure
from core.llm.client import OitomaClient
from tests.unit.doc_factory import make_doc
from tests.unit.test_analyze_pipeline import S1, S2, S3, S4, FakeLLM
from tests.unit.test_rules import CHAT_URL, chat, config


async def test_fast_paragraph_and_suggestion_do_not_wait_for_slow_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = make_doc([("1 手法", "methods", [[S3, S4], [S1, S2]])])
    release = asyncio.Event()
    suggested = asyncio.Event()
    fake = FakeLLM()

    async def structure(client: OitomaClient, target: Target, **kwargs: Any) -> ParagraphStructure:
        if target.paragraph.paragraph_id == "P-0002":
            await release.wait()
        return await extract_structure(client, target, **kwargs)

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "先回りして見つける" in body["messages"][0]["content"]:
            return chat(
                {
                    "issues": [
                        {
                            "type": "overclaim",
                            "sentence_ids": ["S-00001"],
                            "quote": "全てのタスクで既存手法を上回り",
                            "issue": "主張が強い",
                            "rationale": "評価が限定的",
                            "severity": "critical",
                        }
                    ]
                }
            )
        if body["model"] == "second":
            assert S1 in body["messages"][-1]["content"]
            assert not release.is_set()
        if "文章を改善" in body["messages"][0]["content"]:
            assert not release.is_set()
            suggested.set()
        return fake(request)

    monkeypatch.setattr("core.analyze.pipeline.extract_structure", structure)
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=respond)
        async with OitomaClient(config(), api_key="k") as client:
            task = asyncio.create_task(
                analyze_logic(
                    doc, client, AnalysisSettings(with_flow_check=False, with_citation_check=False)
                )
            )
            try:
                await asyncio.wait_for(suggested.wait(), 2)
            finally:
                release.set()
                result = await task
    assert result.findings[0].suggestions
