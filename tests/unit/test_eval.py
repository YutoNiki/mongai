"""評価ハーネス（eval/）のテスト。おいとまAPIには接続しない。"""

from pathlib import Path

import pytest

from core.analyze.citation import SupportJudgement
from core.llm.config import get_models_config
from eval import run
from eval.dataset import (
    DATASET_DIR,
    CitationItem,
    FlowItem,
    ParagraphItem,
    RuleItem,
    build_targets,
    load_jsonl,
)
from eval.metrics import Outcome, score
from eval.report import load_results, render


def outcome(item_id: str, gold: str, hit: bool, predicted: set[str]) -> Outcome:
    return Outcome(item_id, gold, hit, frozenset(predicted), 1.0)


def test_score_counts_tp_fp_fn_and_control() -> None:
    outcomes = [
        outcome("a", "leap", True, {"leap"}),
        outcome("b", "leap", False, {"overclaim"}),  # 見逃し＋overclaim の誤検出
        outcome("c", "overclaim", True, {"overclaim"}),
        outcome("d", "none", False, {"leap"}),  # 対照群の誤検出
        outcome("e", "none", False, set()),
    ]
    scores = {s.label: s for s in score(outcomes, ["leap", "overclaim", "none"])}
    assert (scores["leap"].tp, scores["leap"].fp, scores["leap"].fn) == (1, 1, 1)
    assert (scores["overclaim"].tp, scores["overclaim"].fp, scores["overclaim"].fn) == (1, 1, 0)
    assert scores["leap"].precision == 0.5
    assert scores["overclaim"].recall == 1.0
    assert (scores["micro"].tp, scores["micro"].fp, scores["micro"].fn) == (2, 2, 1)
    assert scores["control_fp"].n == 2
    assert scores["control_fp"].fp == 1
    assert "none" not in scores


def test_score_without_predictions_has_no_precision() -> None:
    (leap, micro) = score([outcome("a", "leap", False, set())], ["leap"])
    assert leap.precision is None
    assert leap.recall == 0.0
    assert leap.f1 is None
    assert micro.fn == 1


@pytest.mark.parametrize(
    ("name", "model", "labels"),
    [
        (
            "paragraph.jsonl",
            ParagraphItem,
            {"premise_evidence_mismatch", "leap", "overclaim", "none"},
        ),
        ("flow.jsonl", FlowItem, {"disconnection", "none"}),
        ("citation_support.jsonl", CitationItem, {"does_not_support", "supports"}),
        ("rule.jsonl", RuleItem, {"rule_violation", "none"}),
    ],
)
def test_datasets_are_valid_and_balanced(
    name: str, model: type[ParagraphItem | FlowItem | CitationItem | RuleItem], labels: set[str]
) -> None:
    items = load_jsonl(DATASET_DIR / name, model)
    assert {i.label for i in items} == labels
    for label in labels:
        for lang in ("ja", "en"):
            assert any(i.label == label and i.lang == lang for i in items), (label, lang)
    # 問題を仕込んだカテゴリは、日英合わせて10件ずつ（SPEC 5.4）
    for label in labels - {"none", "supports"}:
        assert sum(i.label == label for i in items) == 10


def test_paragraph_targets_point_to_existing_sentences() -> None:
    for item in load_jsonl(DATASET_DIR / "paragraph.jsonl", ParagraphItem):
        if item.label == "none":
            assert item.target == []
        else:
            assert item.target, item.id
            assert all(1 <= n <= len(item.sentences) for n in item.target), item.id


def test_flow_targets_are_inside_and_not_first() -> None:
    for item in load_jsonl(DATASET_DIR / "flow.jsonl", FlowItem):
        if item.label == "none":
            assert item.target is None
        else:
            assert item.target is not None
            assert 2 <= item.target <= len(item.paragraphs), item.id


def test_load_jsonl_rejects_duplicate_ids(tmp_path: Path) -> None:
    line = CitationItem(
        id="x", lang="en", label="supports", sentence="s", context="c", reference="r", abstract="a"
    ).model_dump_json()
    path = tmp_path / "d.jsonl"
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="重複"):
        load_jsonl(path, CitationItem)


def test_build_targets_links_paragraphs() -> None:
    targets = build_targets("T", "ja", "考察", "discussion", [["文1。", "文2。"], ["文3。"]])
    assert [t.paragraph.paragraph_id for t in targets] == ["T-P1", "T-P2"]
    assert [s.sentence_id for s in targets[0].sentences] == ["T-P1-S1", "T-P1-S2"]
    assert targets[0].paragraph.text == "文1。文2。"
    assert targets[1].previous == targets[0].paragraph
    assert targets[0].next == targets[1].paragraph
    assert targets[0].section is not None
    assert targets[0].section.role == "discussion"


def test_with_model_replaces_only_the_role() -> None:
    base = get_models_config()
    config = run.with_model(base, "logic_check", "other-model", samples=1)
    assert config.role("logic_check").model == "other-model"
    assert config.role("logic_check").samples == 1
    assert config.role("logic_check").thinking == base.role("logic_check").thinking
    assert config.role("structure_extraction") == base.role("structure_extraction")
    assert base.role("logic_check").model != "other-model"


class _FakeClient:
    """chat_json だけを持つ偽のクライアント。不一致のデータには does_not_support を返す。"""

    async def chat_json(
        self, role: str, messages: list[dict[str, str]], schema: type, *, lang: str
    ) -> SupportJudgement:
        assert role == "citation_support"
        assert "<external_data" in messages[1]["content"]
        verdict = (
            "does_not_support"
            if "朝食" in messages[1]["content"] and "睡眠時間が長い" in messages[1]["content"]
            else "supports"
        )
        return SupportJudgement(verdict=verdict, reason="r")


async def test_run_citation_scores_verdicts() -> None:
    outcomes = await run.run_citation(_FakeClient(), "ja", None)  # type: ignore[arg-type]
    by_id = {o.item_id: o for o in outcomes}
    assert by_id["ja-cit-ns-01"].hit
    assert not by_id["ja-cit-ns-02"].hit
    assert by_id["ja-cit-s-01"].gold == "none"
    assert not by_id["ja-cit-s-01"].predicted


def test_report_shows_latest_run_per_model(tmp_path: Path) -> None:
    header = "run_at,role,task,model,label,n,tp,fp,fn,precision,recall,f1,latency_s,errors\n"
    (tmp_path / "1.csv").write_text(
        header
        + "2026-10-13T10:00:00+09:00,logic_check,paragraph,m1,leap,10,2,0,8,1.00,0.20,0.33,5.0,0\n",
        encoding="utf-8",
    )
    (tmp_path / "2.csv").write_text(
        header
        + "2026-10-13T11:00:00+09:00,logic_check,paragraph,m1,leap,10,6,1,4,0.86,0.60,0.71,5.0,0\n"
        + "2026-10-13T11:00:00+09:00,logic_check,paragraph,m1,control_fp,10,0,2,0,-,-,-,5.0,0\n",
        encoding="utf-8",
    )
    rows = load_results(tmp_path)
    assert {r["recall"] for r in rows} == {"0.60", "-"}
    page = render(rows)
    assert "論理の飛躍" in page
    assert "0.71" in page
    assert "0.33" not in page
    assert "2 / 10 件で指摘あり" in page
    assert "http" not in page  # 外部の資源を読み込まない


def test_rule_items_point_to_their_rules() -> None:
    for item in load_jsonl(DATASET_DIR / "rule.jsonl", RuleItem):
        ids = {r.rule_id for r in item.rules}
        if item.label == "none":
            assert item.target_rule is None and item.target == []
        else:
            assert item.target_rule in ids, item.id
            assert all(1 <= n <= len(item.sentences) for n in item.target), item.id
