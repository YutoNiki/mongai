"""提出要件の保存・隔離・固定、文書全体の判定、レポートの対象選択。合成データのみ。"""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import respx

from core.analyze.context import select_targets
from core.analyze.models import Finding
from core.analyze.pipeline import AnalysisSettings, analyze_logic
from core.instructions.check import check_requirements, mechanical_check
from core.instructions.extract import extract_requirements
from core.instructions.findings import link_findings
from core.instructions.models import (
    Draft,
    ProfileRef,
    Requirement,
    RequirementResult,
    Snapshot,
    detect_conflicts,
)
from core.instructions.review import document_for_review, review_prompt
from core.instructions.store import (
    InstructionConflictError,
    InstructionNotFoundError,
    InstructionStore,
)
from core.jobs import run_analysis, run_instruction_extraction
from core.llm.client import OitomaClient
from core.llm.config import ModelsConfig, RoleConfig
from core.rules.store import RuleStore
from core.store import Store, today_jst
from core.store.files import new_doc_id, save_paper
from core.store.migrate import migrate
from core.store.schema import INSTRUCTION_SCHEMA
from tests.unit.auth_factory import make_user
from tests.unit.doc_factory import make_doc
from tests.unit.pdf_factory import Text, make_pdf
from tests.unit.test_analyze_pipeline import CHAT_URL, S1, S2, S3, S4, FakeLLM, config, reply
from tests.unit.test_jobs import FakeGateway
from tests.unit.test_rules import rule


def instruction_config(*, small: bool = False) -> ModelsConfig:
    cfg = config()
    roles = {
        **cfg.roles,
        "requirement_check": RoleConfig(model="think", max_tokens=500 if small else 4096),
        "requirement_extraction": RoleConfig(model="fast"),
    }
    return cfg.model_copy(update={"roles": roles, "context_window": 2048 if small else 32768})


def confirmed(store: InstructionStore, owner: str, requirements: list[Requirement]) -> Draft:
    draft = store.create_draft(owner, "course_report", "", [])
    draft = store.update_draft(owner, draft.draft_id, draft.revision, requirements)
    return store.update_draft(owner, draft.draft_id, draft.revision, None, confirm=True)


@pytest.fixture
def instruction_store(tmp_path: Path) -> tuple[Store, InstructionStore, str, str]:
    db = Store(tmp_path / "db.sqlite3")
    owner, other = make_user(db.path, "alice"), make_user(db.path, "bob")
    return db, InstructionStore(db.path), owner, other


def test_profiles_are_private_versioned_and_independent(
    instruction_store: tuple[Store, InstructionStore, str, str],
) -> None:
    _, store, owner, other = instruction_store
    draft = confirmed(
        store, owner, [Requirement(text="2000字以内", kind="character_count", maximum=2000)]
    )
    profile = store.save_profile(owner, "講義A", "course", draft.draft_id, draft.revision)
    assert store.list_profiles(other) == []
    for action in (
        lambda: store.get_profile(other, profile.profile_id),
        lambda: store.delete_profile(other, profile.profile_id),
        lambda: store.get_draft(other, draft.draft_id),
        lambda: store.save_profile(other, "x", "course", draft.draft_id, draft.revision),
    ):
        with pytest.raises(InstructionNotFoundError):
            action()
    other_draft = confirmed(
        store, owner, [Requirement(text="1000字以内", kind="character_count", maximum=1000)]
    )
    other_profile = store.save_profile(
        owner, "講義B", "course", other_draft.draft_id, other_draft.revision
    )
    chosen = store.create_draft(
        owner, "course_report", "", [ProfileRef(profile_id=profile.profile_id, revision=1)]
    )
    assert [r.maximum for r in chosen.requirements] == [2000]
    assert other_profile.profile_id not in [p.profile_id for p in chosen.profiles]
    updated = store.save_profile(
        owner,
        "講義A",
        "course",
        other_draft.draft_id,
        other_draft.revision,
        profile_id=profile.profile_id,
        revision=1,
    )
    assert updated.revision == 2
    with pytest.raises(InstructionConflictError):
        store.create_draft(
            owner, "course_report", "", [ProfileRef(profile_id=profile.profile_id, revision=1)]
        )
    with pytest.raises(InstructionConflictError):
        store.save_profile(
            owner,
            "講義A",
            "course",
            draft.draft_id,
            draft.revision,
            profile_id=profile.profile_id,
            revision=1,
        )
    assert chosen.requirements[0].maximum == 2000


def test_queued_analysis_keeps_confirmed_snapshot(
    instruction_store: tuple[Store, InstructionStore, str, str],
) -> None:
    db, store, owner, _ = instruction_store
    db.save_paper("doc", owner_user_id=owner, filename="report.pdf", size=1)
    draft = confirmed(store, owner, [Requirement(text="である調", kind="style", value="である")])
    aid = store.create_analysis(
        owner,
        "doc",
        AnalysisSettings(document_type="course_report"),
        draft.draft_id,
        draft.revision,
        "course_report",
    )
    store.update_draft(owner, draft.draft_id, draft.revision, [Requirement(text="別の条件")])
    record = db.get_analysis(aid)
    assert record is not None
    assert record.settings["instruction_snapshot"]["requirements"][0]["text"] == "である調"
    with pytest.raises(InstructionConflictError):
        store.create_analysis(
            owner, "doc", AnalysisSettings(), draft.draft_id, draft.revision, "course_report"
        )
    with pytest.raises(InstructionConflictError):
        store.create_analysis(
            owner, "doc", AnalysisSettings(), draft.draft_id, draft.revision, "paper"
        )


def test_competing_requirements_need_confirmation(
    instruction_store: tuple[Store, InstructionStore, str, str],
) -> None:
    _, store, owner, _ = instruction_store
    reqs = [
        Requirement(text="2000字以上", kind="character_count", minimum=2000),
        Requirement(text="1000字以内", kind="character_count", maximum=1000),
    ]
    assert len(detect_conflicts(reqs)) == 1
    assert (
        len(
            detect_conflicts(
                [
                    Requirement(text="文献5件以上", kind="reference_count", minimum=5),
                    Requirement(
                        text="文献3件以内", kind="reference_count", maximum=3, count_scope="all"
                    ),
                ]
            )
        )
        == 1
    )
    # 両立可能な上限同士を、競合として誤検出しない。
    assert (
        detect_conflicts(
            [reqs[1], Requirement(text="500字以内", kind="character_count", maximum=500)]
        )
        == []
    )
    draft = store.create_draft(owner, "course_report", "", [])
    draft = store.update_draft(owner, draft.draft_id, draft.revision, reqs)
    with pytest.raises(InstructionConflictError):
        store.update_draft(owner, draft.draft_id, draft.revision, None, confirm=True)
    reqs[0].enabled = False
    draft = store.update_draft(owner, draft.draft_id, draft.revision, reqs)
    assert store.update_draft(owner, draft.draft_id, draft.revision, None, confirm=True).confirmed


def test_version_one_migration_preserves_owners(
    instruction_store: tuple[Store, InstructionStore, str, str],
) -> None:
    db, _, owner, _ = instruction_store
    db.save_paper("doc", owner_user_id=owner, filename="report.pdf", size=1)
    aid = db.create_analysis("doc", owner_user_id=owner, kind="logic", settings=AnalysisSettings())
    conn = sqlite3.connect(db.path)
    conn.executescript(
        "DROP TABLE analysis_requirement_results; DROP TABLE instruction_drafts; DROP TABLE instruction_profiles; PRAGMA user_version=1;"
    )
    conn.close()
    assert "移行しました" in migrate(db.path)
    assert db.get_paper_for("doc", owner) is not None
    assert db.get_analysis_for(aid, owner) is not None
    assert db.get_analysis_for(aid, None) is None
    assert "移行済み" in migrate(db.path)
    assert list((db.path.parent / "backups").glob("*.sqlite3"))


def test_report_includes_short_opening_and_does_not_mutate_document() -> None:
    doc = make_doc([], front=[["これは序論である。"], ["以上を結論とする。"]])
    assert select_targets(doc) == []
    view = document_for_review(doc, "course_report")
    assert len(select_targets(view, document_type="course_report")) == 2
    assert doc.sections[0].role == "abstract" and view.sections[0].role == "other"
    assert "IMRaD" in review_prompt("flow_check", "ja", "course_report", output_language="日本語")
    assert "report structure" in review_prompt(
        "flow_check", "en", "lab_report", output_language="English"
    )


@pytest.mark.parametrize(
    "minimum,maximum,spaces,verdict",
    [(None, 4, False, "met"), (5, None, False, "not_met"), (None, 4, True, "not_met")],
)
def test_character_count_boundaries(
    minimum: int | None, maximum: int | None, spaces: bool, verdict: str
) -> None:
    doc = make_doc([("1 本論", "other", [["あ い。え"]])])
    req = Requirement(
        text="字数", kind="character_count", minimum=minimum, maximum=maximum, include_spaces=spaces
    )
    result = mechanical_check(req, doc)
    assert result is not None and result.verdict == verdict


def test_missing_page_or_reference_extraction_is_not_a_violation() -> None:
    doc = make_doc([("1 本論", "other", [["本文。"]])])
    doc.page_count = 2
    result = mechanical_check(
        Requirement(text="100字以上", kind="character_count", minimum=100), doc
    )
    assert result is not None and result.verdict == "unknown"
    doc.page_count = 1
    doc.warnings = ["参考文献の抽出に失敗"]
    result = mechanical_check(
        Requirement(text="文献3件以上", kind="reference_count", minimum=3), doc
    )
    assert result is not None and result.verdict == "unknown"
    result = mechanical_check(Requirement(text="フォント12pt", kind="manual"), doc)
    assert result is not None and result.verdict == "unknown" and result.coverage == "none"


@pytest.mark.parametrize(
    "verdict,quote,sids,expected",
    [
        ("met", "具体例である。", ["S-00001"], "met"),
        ("met", "存在しない文。", ["S-00001"], "unknown"),
        ("not_met", "存在しない文。", ["S-00001"], "unknown"),
        ("not_met", "", [], "not_met"),
        ("unknown", "", [], "unknown"),
    ],
)
async def test_content_evidence_is_validated(
    verdict: str, quote: str, sids: list[str], expected: str
) -> None:
    doc = make_doc([("1 本論", "other", [["具体例である。"]])])
    req = Requirement(text="具体例を挙げる", aggregation="any")
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=reply(
                {"verdict": verdict, "reason": "確認結果", "quote": quote, "sentence_ids": sids}
            )
        )
        async with OitomaClient(instruction_config(), api_key="k") as client:
            results = await check_requirements(
                client, doc, Snapshot(document_type="course_report", requirements=[req])
            )
    assert results[0].verdict == expected
    assert bool(results[0].quote) == (expected == "met")


async def test_absence_is_unknown_when_extraction_is_uncertain() -> None:
    doc = make_doc([("1 本論", "other", [["本文。"]])])
    doc.warnings = ["抽出結果に注意"]
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=reply({"verdict": "not_met", "reason": "考察がない"})
        )
        async with OitomaClient(instruction_config(), api_key="k") as client:
            results = await check_requirements(
                client,
                doc,
                Snapshot(
                    document_type="course_report", requirements=[Requirement(text="考察を含める")]
                ),
            )
    assert results[0].verdict == "unknown"


async def test_all_requirements_are_checked_without_top_k_filter() -> None:
    doc = make_doc([("1 本論", "other", [["本文。"]])])
    reqs = [Requirement(text=f"条件{i}") for i in range(15)]
    with respx.mock:
        route = respx.post(CHAT_URL).mock(
            return_value=reply({"verdict": "unknown", "reason": "資料が不足"})
        )
        async with OitomaClient(instruction_config(), api_key="k") as client:
            results = await check_requirements(
                client, doc, Snapshot(document_type="course_report", requirements=reqs)
            )
    assert route.call_count == 15
    assert {r.requirement_id for r in results} == {r.requirement_id for r in reqs}


async def test_chunked_missing_content_is_not_global_absence() -> None:
    doc = make_doc(
        [("1 本論", "other", [["合成の本文である。" * 250], ["別の本文である。" * 250]])]
    )
    with respx.mock:
        route = respx.post(CHAT_URL).mock(
            return_value=reply({"verdict": "not_met", "reason": "抜粋にない"})
        )
        async with OitomaClient(instruction_config(small=True), api_key="k") as client:
            results = await check_requirements(
                client,
                doc,
                Snapshot(
                    document_type="course_report",
                    requirements=[Requirement(text="具体例を含める", aggregation="any")],
                ),
            )
    assert route.call_count > 1
    assert results[0].verdict == "unknown"


def test_absence_does_not_get_fabricated_pdf_annotation() -> None:
    doc = make_doc([("1 本論", "other", [["本文。"]])])
    req = Requirement(text="結論を含める")
    results = [
        RequirementResult(
            requirement_id=req.requirement_id,
            verdict="not_met",
            reason="結論がない",
            coverage="full",
        )
    ]
    findings: list[Any] = []
    linked = link_findings(
        doc, Snapshot(document_type="course_report", requirements=[req]), results, findings, "test"
    )
    assert findings == [] and linked[0].finding_id is None


async def test_extraction_rejects_invented_source_excerpt() -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=reply(
                {"requirements": [{"text": "文献3件以上", "source_excerpt": "原文にない条件"}]}
            )
        )
        async with OitomaClient(instruction_config(), api_key="k") as client:
            with pytest.raises(ValueError):
                await extract_requirements(client, "2000字以内")


async def test_extraction_job_uses_owner_and_recovers(
    instruction_store: tuple[Store, InstructionStore, str, str],
) -> None:
    db, store, owner, _ = instruction_store
    draft = store.create_draft(owner, "course_report", "2000字以内", [])
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=reply(
                {
                    "requirements": [
                        {
                            "text": "2000字以内",
                            "kind": "character_count",
                            "maximum": 2000,
                            "source_excerpt": "2000字以内",
                        }
                    ]
                }
            )
        )
        async with OitomaClient(instruction_config(), api_key="k") as client:
            assert (
                await run_instruction_extraction({}, draft.draft_id, store=store, client=client)
                == "ready"
            )
    assert store.get_draft(owner, draft.draft_id).requirements[0].maximum == 2000
    assert db.get_daily_usage(today_jst(), user_id=owner).requests == 1
    interrupted = store.create_draft(owner, "paper", "条件", [])
    assert store.claim_extraction(interrupted.draft_id) is not None
    queued = store.create_draft(owner, "paper", "別の条件", [])
    assert store.recover_interrupted() == [queued.draft_id]
    assert store.get_draft(owner, interrupted.draft_id).status == "failed"


def test_comparison_requires_same_document_type_and_conditions(
    instruction_store: tuple[Store, InstructionStore, str, str],
) -> None:
    db, store, owner, _ = instruction_store
    db.save_paper("doc", owner_user_id=owner, filename="report.pdf", size=1)
    older = db.create_analysis(
        "doc", owner_user_id=owner, kind="logic", settings=AnalysisSettings()
    )
    db.finish_analysis(older, status="completed")
    draft = confirmed(store, owner, [Requirement(text="考察を含める")])
    aid = store.create_analysis(
        owner,
        "doc",
        AnalysisSettings(document_type="course_report"),
        draft.draft_id,
        draft.revision,
        "course_report",
    )
    assert db.find_previous(aid) is None
    db.finish_analysis(aid, status="completed")
    next_id = store.create_analysis(
        owner,
        "doc",
        AnalysisSettings(document_type="course_report"),
        draft.draft_id,
        draft.revision,
        "course_report",
    )
    previous = db.find_previous(next_id)
    assert previous is not None and previous.analysis_id == aid


def test_requirement_results_are_removed_with_analysis(
    instruction_store: tuple[Store, InstructionStore, str, str],
) -> None:
    db, store, owner, _ = instruction_store
    db.save_paper("doc", owner_user_id=owner, filename="report.pdf", size=1)
    draft = confirmed(store, owner, [Requirement(text="考察を含める")])
    aid = store.create_analysis(
        owner,
        "doc",
        AnalysisSettings(document_type="course_report"),
        draft.draft_id,
        draft.revision,
        "course_report",
    )
    store.save_results(
        aid,
        [RequirementResult(requirement_id=draft.requirements[0].requirement_id, reason="未確認")],
    )
    db.finish_analysis(aid, status="completed")
    db.delete_paper("doc", owner_user_id=owner)
    assert store.results(aid) == []
    with pytest.raises(InstructionNotFoundError):
        store.get_draft(owner, draft.draft_id)


def test_local_violation_links_to_real_pdf_location() -> None:
    doc = make_doc([("1 本論", "other", [["根拠なく断定した。"]])])
    req = Requirement(text="根拠を示す")
    result = RequirementResult(
        requirement_id=req.requirement_id,
        verdict="not_met",
        reason="根拠がない",
        sentence_ids=[doc.sentences[0].sentence_id],
        quote="根拠なく断定した。",
        coverage="full",
    )
    findings: list[Finding] = []
    linked = link_findings(
        doc, Snapshot(document_type="course_report", requirements=[req]), [result], findings, "test"
    )
    assert linked[0].finding_id == findings[0].finding_id
    assert findings[0].bboxes == doc.sentences[0].bboxes
    assert findings[0].evidence[0].ref == req.requirement_id
    link_findings(
        doc, Snapshot(document_type="course_report", requirements=[req]), [result], findings, "test"
    )
    assert len(findings) == 1


async def test_partial_document_does_not_pass_universal_requirement() -> None:
    doc = make_doc([("1 本論", "other", [["である。"]])])
    doc.page_count = 2
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=reply(
                {
                    "verdict": "met",
                    "reason": "文体を確認",
                    "sentence_ids": [doc.sentences[0].sentence_id],
                    "quote": "である。",
                }
            )
        )
        async with OitomaClient(instruction_config(), api_key="k") as client:
            results = await check_requirements(
                client,
                doc,
                Snapshot(
                    document_type="course_report",
                    requirements=[Requirement(text="である調で統一", aggregation="all")],
                ),
            )
    assert results[0].verdict == "unknown" and results[0].coverage == "partial"


async def test_pdf_report_job_persists_all_requirement_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import httpx

    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("GROBID_URL", "http://127.0.0.1:9")
    db = Store(tmp_path / "db.sqlite3")
    owner = make_user(db.path)
    instructions = InstructionStore(db.path)
    draft = confirmed(
        instructions,
        owner,
        [
            Requirement(text="本文10字以内", kind="character_count", maximum=10),
            Requirement(text="フォント12pt", kind="manual"),
            Requirement(text="授業で扱った理論と比較する"),
        ],
    )
    pdf = make_pdf(
        tmp_path / "report.pdf",
        [[Text(72, 120, "これは合成した授業レポートの本文である。", font="japan")]],
    )
    doc_id = save_paper(pdf.read_bytes(), new_doc_id())
    db.save_paper(doc_id, owner_user_id=owner, filename="report.pdf", size=pdf.stat().st_size)
    private_rules = RuleStore(db.path)
    private_rules.add_rules(owner, [rule("R-paper-only", "論文の要旨に実験結果を書く")], [None])
    settings = AnalysisSettings(
        document_type="course_report",
        with_citation_check=False,
        with_rule_check=False,
        with_lab_check=False,
        with_term_lookup=False,
        with_flow_check=False,
        with_double_check=False,
        with_suggestions=False,
    )
    aid = instructions.create_analysis(
        owner, doc_id, settings, draft.draft_id, draft.revision, "course_report"
    )
    fake = FakeLLM()

    def chat(request: httpx.Request) -> httpx.Response:
        system = json.loads(request.content)["messages"][0]["content"]
        if "指定された1つの要件" in system:
            return reply({"verdict": "unknown", "reason": "授業の理論が指定されていない"})
        assert "授業レポート" in system or "参考文献リスト" in system
        return fake(request)

    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=chat)
        respx.post("http://127.0.0.1:9/api/processFulltextDocument").mock(
            return_value=httpx.Response(503)
        )
        async with OitomaClient(instruction_config(), api_key="k") as client:
            assert (
                await run_analysis({}, aid, store=db, client=client, gateway=FakeGateway())
                == "completed"
            )
    results = instructions.results(aid)
    assert len(results) == 3 and all(r.state == "completed" for r in results)
    assert {r.requirement_id: r.verdict for r in results} == {
        draft.requirements[0].requirement_id: "not_met",
        draft.requirements[1].requirement_id: "unknown",
        draft.requirements[2].requirement_id: "unknown",
    }
    record = db.get_analysis(aid)
    assert record is not None and record.progress.stage == "locate"
    assert record.settings["ruleset"]["approved"] == 3
    assert db.get_document(doc_id) is not None


def test_failed_version_one_migration_rolls_back(
    instruction_store: tuple[Store, InstructionStore, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.store.migrate as module

    db, _, owner, _ = instruction_store
    db.save_paper("doc", owner_user_id=owner, filename="report.pdf", size=1)
    conn = sqlite3.connect(db.path)
    conn.executescript(
        "DROP TABLE analysis_requirement_results; DROP TABLE instruction_drafts; DROP TABLE instruction_profiles; PRAGMA user_version=1;"
    )
    conn.close()

    monkeypatch.setattr(module, "INSTRUCTION_SCHEMA", INSTRUCTION_SCHEMA + ";\nINVALID SQL;\n")
    with pytest.raises(sqlite3.OperationalError):
        migrate(db.path)
    conn = sqlite3.connect(db.path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert (
        conn.execute("SELECT owner_user_id FROM papers WHERE doc_id='doc'").fetchone()[0] == owner
    )
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name='instruction_profiles'").fetchone()
        is None
    )
    conn.close()


async def test_large_custom_context_does_not_discard_other_findings() -> None:
    doc = make_doc([("1 はじめに", "introduction", [[S1, S2], [S3, S4]])])
    requirements = [Requirement(text=f"条件{i}：" + "あ" * 900) for i in range(50)]
    settings = AnalysisSettings(
        with_citation_check=False,
        with_term_lookup=False,
        with_flow_check=False,
        with_double_check=False,
        instruction_snapshot=Snapshot(document_type="paper", requirements=requirements),
    )
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=FakeLLM())
        async with OitomaClient(instruction_config(), api_key="k") as client:
            result = await analyze_logic(doc, client, settings)
    assert result.findings
    assert any("改善案を作れませんでした" in w for w in result.warnings)
