"""論理・読みやすさの解析の流れ（LLM は respx で模擬する）。"""

import itertools
import json
from typing import Any

import httpx
import respx

from core.analyze.models import Finding
from core.analyze.pipeline import AnalysisSettings, analyze_logic
from core.llm.client import DEFAULT_BASE_URL, OitomaClient
from core.llm.config import ModelsConfig
from tests.unit.doc_factory import make_doc

CHAT_URL = f"{DEFAULT_BASE_URL}/chat/completions"

S1 = "大量のデータで学習したモデルは要約で高い性能を示しているが，データの構築は高価である．"
S2 = "そこで本研究では，抽出と言い換えを組み合わせて疑似データを安価に作る手法を提案する．"
S3 = "提案手法は全てのタスクで既存手法を上回り，データ拡張の問題を完全に解決する．"
S4 = "実験では二つのデータセットを用い，ROUGE と BERTScore で性能を評価した．"


def config() -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "api": {"max_retries": 0, "json_mode": "json_schema"},
            "roles": {
                "structure_extraction": {"model": "fast"},
                "logic_check": {"model": "think", "samples": 3, "vote": "majority"},
                "suggestion": {"model": "think"},
                "reference_extraction": {"model": "fast"},
                "citation_support": {"model": "think"},
            },
            "embeddings": {},
        }
    )


def reply(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        },
    )


class FakeLLM:
    """システムプロンプトの内容で、どの工程の呼び出しかを見分けて応答する。"""

    def __init__(self) -> None:
        # 段落内チェックの3回の判定：2回が overclaim、1回が leap を指摘する
        self.checks = itertools.cycle(
            [
                [
                    {
                        "type": "overclaim",
                        "sentence_ids": ["S-00003"],
                        "quote": "全てのタスクで既存手法を上回り",
                        "issue": "根拠に比べて主張が強い",
                        "rationale": "評価は二つのデータセットだけ",
                        "severity": "critical",
                        "related_sentence_ids": ["S-00004"],
                    }
                ],
                [
                    {
                        "type": "overclaim",
                        "sentence_ids": ["S-00003"],
                        "quote": "完全に解決する",
                        "issue": "過大な主張",
                        "rationale": "根拠不足",
                        "severity": "major",
                    }
                ],
                [
                    {
                        "type": "leap",
                        "sentence_ids": ["S-00002"],
                        "quote": "安価に作る",
                        "issue": "飛躍",
                        "rationale": "説明不足",
                        "severity": "suggestion",
                    }
                ],
            ]
        )
        self.calls: dict[str, int] = {}
        self.check_inputs: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        user = body["messages"][-1]["content"]
        if "参考文献リストを構造化" in system:
            stage = "reference_extraction"
            payload: dict[str, Any] = {"references": []}
        elif "論証構造を分析" in system:
            stage = "structure"
            payload = {
                "claims": [{"sentence_ids": ["S-00003"], "text": "上回り"}],
                "premises": [],
                "evidence": [],
                "connectives": ["そこで"],
                "novelty_claim": "提案" in user,
                "summary": "要約",
            }
        elif "先回りして見つける" in system:
            stage = "paragraph_check"
            self.check_inputs.append(user)
            judged = user.split("判定する段落")[1].split("直後の段落")[0]
            in_paragraph = "S-00003" in judged
            payload = {"issues": next(self.checks) if in_paragraph else []}
        elif "段落どうしのつながり" in system:
            stage = "flow_check"
            payload = {
                "issues": [
                    {
                        "type": "section_role_violation",
                        "paragraph_id": "P-0002",
                        "issue": "はじめにで結果を述べている",
                        "rationale": "役割違反",
                        "severity": "suggestion",
                    }
                ]
            }
        elif "文章を改善" in system:
            stage = "suggestion"
            payload = {
                "suggestions": [
                    "提案手法は評価した二つのデータセットで既存手法を上回った．",
                    "提案手法は全てのタスクで既存手法を上回り，データ拡張の問題を完全に解決する．",
                ]
            }
        else:
            raise AssertionError(system[:50])
        self.calls[stage] = self.calls.get(stage, 0) + 1
        return reply(payload)


async def test_analyze_logic_end_to_end() -> None:
    doc = make_doc(
        [
            ("1 はじめに", "introduction", [[S1, S2], [S3, S4]]),
            (
                "参考文献",
                "references",
                [["Smith, J. (2020). A paper title that is long enough to pass."]],
            ),
        ]
    )
    fake = FakeLLM()
    progress: list[tuple[str, int, int]] = []
    streamed: list[Finding] = []
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=fake)
        async with OitomaClient(config(), api_key="k") as client:
            result = await analyze_logic(
                doc,
                client,
                AnalysisSettings(),
                on_progress=lambda s, d, t: progress.append((s, d, t)),
                on_findings=streamed.extend,
            )

    # 段落内チェックには、直前・直後の段落が文脈として渡される
    first = fake.check_inputs[0] if fake.check_inputs else ""
    assert any("直後の段落" in t and S3 in t.split("直後の段落")[1] for t in fake.check_inputs)
    assert first
    # 2段落 × 構造抽出1回、段落内チェック 2段落 × 3回、段落間チェック 3回、改善案は指摘の数だけ
    assert fake.calls == {"structure": 2, "paragraph_check": 6, "flow_check": 3, "suggestion": 2}

    by_type = {f.subcategory: f for f in result.findings}
    assert set(by_type) == {"overclaim", "section_role_violation"}  # leap は 1/3 なので落ちる

    over = by_type["overclaim"]
    assert over.category == "logic"
    assert (over.votes, over.samples, over.confidence) == (2, 3, "low")
    assert over.severity == "critical"  # 同数のときは先に出たもの
    assert over.sentence_ids == ["S-00003"]
    assert over.quote == "全てのタスクで既存手法を上回り"
    assert [e.ref for e in over.evidence] == ["S-00004"]
    assert over.bboxes and over.page == 1
    assert over.models.primary == "think"
    # 原文と同じ案は除かれる
    assert over.suggestions == ["提案手法は評価した二つのデータセットで既存手法を上回った．"]

    flow = by_type["section_role_violation"]
    assert flow.category == "readability"
    assert (flow.votes, flow.confidence) == (3, "high")
    assert flow.sentence_ids == ["S-00003"]

    # 重大度の順に並ぶ
    assert [f.severity for f in result.findings] == ["critical", "suggestion"]
    assert len({f.finding_id for f in result.findings}) == 2
    # 段落の判定が終わるたびに、改善案を付ける前の指摘が通知される
    assert {f.subcategory for f in streamed} == {"overclaim", "section_role_violation"}
    assert ("paragraph_check", 2, 2) in progress and ("suggestion", 2, 2) in progress
    assert {s.paragraph_id for s in result.structures} == {"P-0001", "P-0002"}
    usage = {r.role: r.requests for r in result.api_usage}
    assert usage == {"structure_extraction": 2, "logic_check": 9, "suggestion": 2}


async def test_failures_become_warnings() -> None:
    doc = make_doc([("1 はじめに", "introduction", [[S1, S2]])])
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
        async with OitomaClient(config(), api_key="k") as client:
            result = await analyze_logic(doc, client, AnalysisSettings(with_flow_check=False))
    assert result.findings == []
    assert any("論証構造" in w for w in result.warnings)
    assert any("チェックに失敗" in w for w in result.warnings)
