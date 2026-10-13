"""研究室知識（M8）：保存（研究室ごとの分離）・取り込み・類似箇所の判定・解析への組み込み。"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from core.analyze.context import select_targets
from core.analyze.pipeline import AnalysisSettings, analyze_logic
from core.jobs import run_lab_extraction
from core.lab.check import candidates_for, check_lab
from core.lab.extract import LAB_SOURCE_PREFIX, extract_lab_knowledge, save_document_file
from core.lab.models import LabComment, LabSource
from core.lab.store import LabStore
from core.llm.client import DEFAULT_BASE_URL, OitomaClient
from core.rules.store import RuleStore
from tests.unit.doc_factory import make_doc
from tests.unit.test_rules import chat, config

CHAT_URL = f"{DEFAULT_BASE_URL}/chat/completions"
EMBED_URL = f"{DEFAULT_BASE_URL}/embeddings"


def comment(comment_id: str, text: str, lab_id: str = "lab-a", **kw: Any) -> LabComment:
    base: dict[str, Any] = {
        "comment_id": comment_id,
        "lab_id": lab_id,
        "document_id": "LD-000000000001",
        "comment": text,
        "category": "logic",
        "source": LabSource(doc="2024査読.pdf", page=2),
    }
    base.update(kw)
    return LabComment(**base)


def embeddings(*vectors: list[float]) -> httpx.Response:
    return httpx.Response(
        200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]}
    )


def test_store_separates_labs(tmp_path: Path) -> None:
    store = LabStore(tmp_path / "db.sqlite3")
    mine = store.create_document("lab-a", "査読.md", "markdown")
    other = store.create_document("lab-b", "他の研究室.md", "markdown")
    store.add_comments(
        [comment("LC-1", "一般化しすぎ"), comment("LC-2", "他の研究室の指摘", lab_id="lab-b")],
        [[1.0, 0.0], [0.0, 1.0]],
    )
    assert [c.comment_id for c in store.list_comments("lab-a")] == ["LC-1"]
    assert set(store.comment_vectors("lab-a")) == {"LC-1"}
    assert [d.document_id for d in store.list_documents("lab-a")] == [mine.document_id]
    # ほかの研究室の資料・指摘は、取得も削除もできない
    assert store.get_document("lab-a", other.document_id) is None
    assert not store.delete_comment("lab-a", "LC-2")
    assert not store.delete_document("lab-a", other.document_id)
    assert store.delete_comment("lab-a", "LC-1")
    assert store.list_comments("lab-a") == []


async def test_extract_lab_knowledge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    lab = LabStore(tmp_path / "db.sqlite3")
    rules = RuleStore(tmp_path / "db.sqlite3")
    doc = lab.create_document("lab-a", "2024査読.md", "markdown")
    save_document_file(
        doc.document_id,
        "markdown",
        "査読者1：1つのデータセットの結果から、手法が常に有効だと述べている。\n回答：表現を弱めました。".encode(),
    )
    payload = {
        "comments": [
            {
                "comment": "1つのデータセットの結果から有効性を一般化している",
                "category": "logic",
                "example": "手法は常に有効である",
            },
        ],
        "rules": [
            {
                "target_section": "Discussion",
                "rule": "評価の範囲を超えて一般化しない",
                "check_type": "llm",
            },
            {
                "target_section": "全体",
                "rule": "略語は初出で定義する",
                "check_type": "mechanical",
                "checker": "abbreviation_definition",
            },
        ],
    }
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=chat(payload))
        respx.post(EMBED_URL).mock(return_value=embeddings([1.0, 0.0], [0.0, 1.0]))
        async with OitomaClient(config_with_lab(), api_key="k") as client:
            counts = await extract_lab_knowledge(client, lab, rules, doc.document_id)
    assert counts == (1, 1)  # 機械判定のルールは組み込みで足りるので加えない
    (saved,) = lab.list_comments("lab-a")
    assert saved.lab_id == "lab-a" and saved.example == "手法は常に有効である"
    assert set(lab.comment_vectors("lab-a")) == {saved.comment_id}
    (lab_rule,) = [r for r in rules.list_rules() if not r.builtin]
    assert lab_rule.status == "pending"
    assert lab_rule.source.doc == f"{LAB_SOURCE_PREFIX}（2024査読.md）"
    assert lab_rule.document_id == doc.document_id


def config_with_lab() -> Any:
    cfg = config()
    roles = dict(cfg.roles)
    roles["lab_extraction"] = cfg.role("rule_extraction")
    roles["lab_check"] = cfg.role("rule_check")
    return cfg.model_copy(update={"roles": roles})


async def test_lab_job_records_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    lab = LabStore(tmp_path / "db.sqlite3")
    doc = lab.create_document("lab-a", "memo.txt", "text")
    save_document_file(doc.document_id, "text", "添削メモ".encode())
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
        async with OitomaClient(config_with_lab(), api_key="k") as client:
            status = await run_lab_extraction({}, doc.document_id, lab_store=lab, client=client)
    assert status == "failed"
    assert lab.get_document("lab-a", doc.document_id).status == "failed"  # type: ignore[union-attr]


def test_candidates_by_similarity() -> None:
    comments = [comment("LC-1", "a"), comment("LC-2", "b"), comment("LC-3", "c")]
    vectors = {"LC-1": [1.0, 0.0], "LC-2": [0.8, 0.6], "LC-3": [0.0, 1.0]}
    chosen = candidates_for([1.0, 0.0], comments, vectors, limit=2, minimum=0.5)
    assert [c.comment_id for c in chosen] == ["LC-1", "LC-2"]


async def test_check_lab_validates_matches() -> None:
    doc = make_doc(
        [
            (
                "5 考察",
                "discussion",
                [["提案手法はすべての場面で有効である。", "今後は対象を広げる。"]],
            )
        ]
    )
    (target,) = select_targets(doc, min_chars=1)
    candidates = [comment("LC-1", "評価の範囲を超えて一般化している", example="常に有効である")]
    payload = {
        "matches": [
            {
                "comment_id": "LC-1",
                "sentence_ids": ["S-00001"],
                "quote": "すべての場面で有効",
                "issue": "1つの評価から一般化している",
                "severity": "major",
            },
            {"comment_id": "LC-x", "sentence_ids": ["S-00001"], "quote": "x", "issue": "x"},
        ]
    }
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=chat(payload))
        async with OitomaClient(config_with_lab(), api_key="k") as client:
            (match,) = await check_lab(
                client, target, candidates, prompt_lang="ja", output_language="日本語"
            )
        sent = json.loads(respx.calls[0].request.content)["messages"][1]["content"]
    assert "[LC-1] 評価の範囲を超えて一般化している（指摘された箇所の例：常に有効である）" in sent
    assert match.comment.comment_id == "LC-1" and match.quote == "すべての場面で有効"


async def test_pipeline_adds_lab_findings() -> None:
    doc = make_doc(
        [
            (
                "5 考察",
                "discussion",
                [
                    [
                        "提案手法はすべての場面で有効である。",
                        "今後は評価の対象を広げ、ほかの地域のデータでも検証する予定である。",
                    ]
                ],
            )
        ]
    )

    def respond(request: httpx.Request) -> httpx.Response:
        system = json.loads(request.content)["messages"][0]["content"]
        if "論証構造を分析" in system:
            return chat(
                {"claims": [], "premises": [], "evidence": [], "connectives": [], "summary": "要約"}
            )
        if "繰り返されていないか" in system:
            return chat(
                {
                    "matches": [
                        {
                            "comment_id": "LC-1",
                            "sentence_ids": ["S-00001"],
                            "quote": "すべての場面で有効",
                            "issue": "一般化しすぎ",
                            "severity": "major",
                        }
                    ]
                }
            )
        if "文章を改善" in system:
            return chat({"suggestions": ["提案手法は評価した場面で有効であった。"]})
        return chat({"issues": []})

    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=respond)
        respx.post(EMBED_URL).mock(return_value=embeddings([1.0, 0.0]))
        async with OitomaClient(config_with_lab(), api_key="k") as client:
            result = await analyze_logic(
                doc,
                client,
                AnalysisSettings(with_flow_check=False, with_citation_check=False),
                lab_comments=[comment("LC-1", "評価の範囲を超えて一般化している")],
                lab_vectors={"LC-1": [1.0, 0.0]},
            )
    assert not result.warnings, result.warnings
    (found,) = result.findings
    assert (found.category, found.subcategory, found.severity) == (
        "lab",
        "similar_past_comment",
        "major",
    )
    assert found.evidence[0].type == "lab" and found.evidence[0].ref == "2024査読.pdf p.2"
    assert "評価の範囲を超えて一般化している" in found.rationale
    assert found.suggestions  # 改善案も付く
