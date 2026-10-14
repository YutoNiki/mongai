"""ルール（M6）：保存・抽出と統合・機械判定・LLM 判定・解析への組み込み・抽出ジョブ。"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from core.analyze.context import select_targets
from core.analyze.pipeline import AnalysisSettings, analyze_logic
from core.ingest.textdoc import chunk_pages, read_pages
from core.jobs import run_rule_extraction
from core.llm.client import DEFAULT_BASE_URL, OitomaClient
from core.llm.config import ModelsConfig
from core.rules.check import applicable_rules, check_rules, group_targets
from core.rules.extract import (
    ExtractedRule,
    extract_rules,
    merge_rules,
    save_document_file,
    to_rule,
)
from core.rules.mechanical import check_abbreviations, check_figure_references, check_units
from core.rules.models import BUILTIN_RULES, Rule, RuleSource, RuleUpdate
from core.rules.store import RuleNotFoundError, RuleStore
from tests.unit.auth_factory import make_user
from tests.unit.doc_factory import make_doc

CHAT_URL = f"{DEFAULT_BASE_URL}/chat/completions"
EMBED_URL = f"{DEFAULT_BASE_URL}/embeddings"
BUILTIN = {r.checker: r for r in BUILTIN_RULES}


def config() -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "api": {"max_retries": 0, "json_mode": "json_schema"},
            "roles": {
                "structure_extraction": {"model": "fast"},
                "logic_check": {"model": "think", "samples": 1},
                "suggestion": {"model": "think"},
                "reference_extraction": {"model": "fast"},
                "citation_support": {"model": "think"},
                "second_opinion": {"model": "second"},
                "rule_extraction": {"model": "fast"},
                "rule_check": {"model": "fast"},
                "term_extraction": {"model": "fast"},
            },
            "embeddings": {"retrieval": {"model": "embed"}},
        }
    )


def chat(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


def rule(rule_id: str, text: str, **kw: Any) -> Rule:
    base: dict[str, Any] = {
        "rule_id": rule_id,
        "target_section": "全体",
        "rule": text,
        "check_type": "llm",
        "source": RuleSource(doc="規定.pdf", page=3),
        "status": "approved",
    }
    base.update(kw)
    return Rule(**base)


# ---------------------------------------------------------------- 保存


def test_store_seeds_builtins_and_protects_them(tmp_path: Path) -> None:
    store = RuleStore(tmp_path / "db.sqlite3")
    a, b = make_user(store.path, "a"), make_user(store.path, "b")
    ids = {r.rule_id for r in store.list_rules(a)}
    assert {"B-ABBR", "B-FIGREF", "B-UNIT"} <= ids
    # 組み込みのルールは文面を変えられず、有効・無効だけを切り替えられる
    updated = store.update_rule(a, "B-UNIT", RuleUpdate(status="disabled", rule="書き換え"))
    assert updated.status == "disabled" and updated.rule != "書き換え"
    # 再起動（作り直し）しても、無効にした状態は残る
    assert RuleStore(tmp_path / "db.sqlite3").get_rule(a, "B-UNIT").status == "disabled"  # type: ignore[union-attr]
    # 無効にしたのは a の設定だけで、b には効かない
    assert store.get_rule(b, "B-UNIT").status == "approved"  # type: ignore[union-attr]


def test_store_rules_documents_and_ruleset(tmp_path: Path) -> None:
    store = RuleStore(tmp_path / "db.sqlite3")
    owner, other = make_user(store.path, "a"), make_user(store.path, "b")
    doc = store.create_document(owner, "規定.md", "markdown")
    r = rule("R-1", "要旨に目的を書く", status="pending", document_id=doc.document_id)
    store.add_rules(owner, [r], [[1.0, 0.0]])
    assert store.embeddings(owner) == {"R-1": [1.0, 0.0]}
    store.set_document_status(doc.document_id, "completed")
    assert store.get_document(owner, doc.document_id).rule_count == 1  # type: ignore[union-attr]
    # ほかの利用者からは、ルールも資料も見えず、変えられない
    assert store.get_rule(other, "R-1") is None and store.embeddings(other) == {}
    assert store.get_document(other, doc.document_id) is None
    assert store.list_documents(other) == []
    with pytest.raises(RuleNotFoundError):
        store.update_rule(other, "R-1", RuleUpdate(status="approved"))
    assert not store.delete_document(other, doc.document_id)
    # 文面を変えると古い埋め込みは消える
    store.update_rule(owner, "R-1", RuleUpdate(rule="要旨に研究の目的を書く", status="approved"))
    assert store.embeddings(owner) == {}
    approved, updated_at = store.ruleset_version(owner)
    assert approved == 4 and updated_at  # 組み込み3件＋承認した1件
    assert store.ruleset_version(other)[0] == 3
    assert store.delete_document(owner, doc.document_id)
    assert store.get_rule(owner, "R-1") is None


def test_store_recovers_interrupted_documents(tmp_path: Path) -> None:
    store = RuleStore(tmp_path / "db.sqlite3")
    owner = make_user(store.path)
    running = store.create_document(owner, "a.md", "markdown")
    queued = store.create_document(owner, "b.md", "markdown")
    store.set_document_status(running.document_id, "running")
    assert store.recover_interrupted() == [queued.document_id]
    assert store.get_document(owner, running.document_id).status == "failed"  # type: ignore[union-attr]


# ---------------------------------------------------------------- 抽出と統合


def test_chunk_pages_keeps_page_markers_and_limit() -> None:
    pages: list[tuple[int | None, str]] = [
        (1, "規定の本文。" * 50),
        (2, ""),
        (3, "図表の規定。" * 50),
    ]
    chunks = chunk_pages(pages, max_tokens=200)
    assert len(chunks) >= 2
    assert chunks[0].startswith("[p.1]")
    assert any("[p.3]" in c for c in chunks)


def test_to_rule_falls_back_to_llm_without_checker() -> None:
    item = ExtractedRule(target_section="全体", rule="略語を定義する", check_type="mechanical")
    assert to_rule(item, filename="f.pdf", document_id="RD-000000000000").check_type == "llm"
    item = item.model_copy(update={"checker": "abbreviation_definition"})
    made = to_rule(item, filename="f.pdf", document_id="RD-000000000000")
    assert (made.check_type, made.checker, made.status) == (
        "mechanical",
        "abbreviation_definition",
        "pending",
    )


def test_merge_rules_by_text_and_embedding() -> None:
    existing = {"R-old": rule("R-old", "要旨には目的を書く。")}
    new = [
        rule("R-a", "要旨には目的を書く", source=RuleSource(doc="b.pdf", page=1)),  # 文面が同じ
        rule("R-b", "結論で新しい主張をしない"),
        rule(
            "R-c", "結論では新たな主張を述べない", source=RuleSource(doc="b.pdf", page=9)
        ),  # 言い換え
        rule("R-d", "図には番号を付ける", target_section="図表"),
    ]
    vectors: list[list[float] | None] = [[1.0, 0.0], [0.0, 1.0], [0.05, 1.0], [0.7, 0.7]]
    kept, updated = merge_rules(new, vectors, existing, {"R-old": [1.0, 0.0]})
    assert [r.rule_id for r, _ in kept] == ["R-b", "R-d"]
    assert kept[0][0].merged_sources == [RuleSource(doc="b.pdf", page=9)]
    assert updated["R-old"].merged_sources == [RuleSource(doc="b.pdf", page=1)]


async def test_extract_rules_saves_pending_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    store = RuleStore(tmp_path / "db.sqlite3")
    owner, other = make_user(store.path, "a"), make_user(store.path, "b")
    # ほかの利用者の、同じ文面のルール（統合の対象にしない）
    store.add_rules(other, [rule("R-other", "要旨に目的を書く")], [[1.0, 0.0]])
    doc = store.create_document(owner, "guide.md", "markdown")
    save_document_file(
        doc.document_id, "markdown", "# 投稿規定\n要旨には目的を書くこと。\n".encode()
    )
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=chat(
                {
                    "rules": [
                        {
                            "target_section": "Abstract",
                            "rule": "要旨に目的を書く",
                            "check_type": "llm",
                        },
                        {
                            "target_section": "Abstract",
                            "rule": "要旨に目的を書く。",
                            "check_type": "llm",
                        },
                    ]
                }
            )
        )
        respx.post(EMBED_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [1, 0]}]
                },
            )
        )
        async with OitomaClient(config(), api_key="k") as client:
            added = await extract_rules(client, store, doc.document_id)
        sent = json.loads(respx.calls[0].request.content)["messages"][1]["content"]
    assert added == 1  # 同じ文面の2件は1件にまとめる
    assert "要旨には目的を書くこと" in sent and "guide.md" in sent
    (made,) = [r for r in store.list_rules(owner) if not r.builtin]
    assert made.status == "pending" and made.source.doc == "guide.md"
    # ほかの利用者のルールには統合されず、出典も足されない
    (theirs,) = [r for r in store.list_rules(other) if not r.builtin]
    assert theirs.rule_id == "R-other" and theirs.merged_sources == []


async def test_rule_extraction_job_records_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    store = RuleStore(tmp_path / "db.sqlite3")
    owner = make_user(store.path)
    doc = store.create_document(owner, "guide.txt", "text")
    save_document_file(doc.document_id, "text", "図表は本文で参照すること。".encode())
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
        async with OitomaClient(config(), api_key="k") as client:
            status = await run_rule_extraction({}, doc.document_id, rule_store=store, client=client)
    assert status == "failed"
    failed = store.get_document(owner, doc.document_id)
    assert failed is not None and failed.status == "failed" and "失敗" in (failed.error or "")


# ---------------------------------------------------------------- 機械判定


def test_abbreviations() -> None:
    doc = make_doc(
        [
            (
                "1 はじめに",
                "introduction",
                [
                    ["本研究では LSTM を用いる。", "長短期記憶（LSTM）は系列を扱う。"],
                    ["評価には双方向変換器（BiTE）を用いる。", "BiTE の性能を示す。"],
                    ["XYZ は重要である。", "XYZ を改良する。", "ABC は一度だけ現れる。"],
                ],
            )
        ]
    )
    found = {v.quote: v for v in check_abbreviations(doc, BUILTIN["abbreviation_definition"], "ja")}
    assert set(found) == {"LSTM", "XYZ"}  # 初出で定義した BiTE、1回だけの ABC は指摘しない
    assert "定義より前" in found["LSTM"].issue
    assert "定義されていません" in found["XYZ"].issue


def test_figure_references() -> None:
    doc = make_doc(
        [
            (
                "4 実験",
                "experiments",
                [["結果を図1に示す。", "表1と2に統計を示す。", "詳細は表3と表9を参照。"]],
            ),
            (
                "キャプション",
                "experiments",
                [["図1 構成"], ["図2 結果の例"], ["表1 統計"], ["表2 詳細"], ["表4 付録"]],
            ),
        ]
    )
    paragraphs = [
        p.model_copy(update={"kind": "caption"}) if p.section_id == "SEC-02" else p
        for p in doc.paragraphs
    ]
    doc = doc.model_copy(update={"paragraphs": paragraphs})
    issues = sorted(
        v.issue for v in check_figure_references(doc, BUILTIN["figure_table_reference"], "ja")
    )
    # 図2・表4は参照されていない。表3は番号の抜け（表4はある）。表9は最大の番号より大きいので指摘しない
    assert issues == [
        "図2が、本文中で参照されていません。",
        "本文が表3を参照していますが、その図表が見つかりません。",
        "表4が、本文中で参照されていません。",
    ]


def test_units() -> None:
    doc = make_doc(
        [
            (
                "4 実験",
                "experiments",
                [
                    [
                        "精度は 85% であった。",
                        "再現率は 80% であった。",
                        "F1 は 82％ であった。",
                        "処理は3分の1になった。",
                    ]
                ],
            )
        ]
    )
    (violation,) = check_units(doc, BUILTIN["unit_consistency"], "ja")
    assert violation.quote == "F1 は 82％ であった。"
    assert "「％」" in violation.issue and "「%」" in violation.issue


# ---------------------------------------------------------------- LLM 判定と解析への組み込み


def test_applicable_rules_by_section_and_language() -> None:
    doc = make_doc(
        [
            ("1 はじめに", "introduction", [["背景を述べる。図1に示す。"]]),
            ("4 結果", "results", [["結果を述べる。"]]),
        ]
    )
    targets = select_targets(doc, min_chars=1)
    rules = [
        rule("R-all", "全体のルール"),
        rule("R-intro", "はじめにのルール", target_section="Introduction"),
        rule("R-res", "結果のルール", target_section="Results"),
        rule("R-fig", "図表のルール", target_section="図表"),
        rule("R-en", "英語だけのルール", lang="en"),
        rule("R-off", "無効のルール", status="disabled"),
        rule("R-adv", "手続きの注意", check_type="advisory"),
    ]
    intro = {r.rule_id for r in applicable_rules(rules, [targets[0]], doc, set())}
    results = {r.rule_id for r in applicable_rules(rules, [targets[1]], doc, set())}
    assert intro == {"R-all", "R-intro", "R-fig"}
    assert results == {"R-all", "R-res"}


async def test_check_rules_validates_output() -> None:
    doc = make_doc([("2 手法", "methods", [["本手法は優れている。", "詳細は後述する。"]])])
    (target,) = select_targets(doc, min_chars=1)
    rules = [rule("R-1", "主観的な形容を避ける")]
    payload = {
        "violations": [
            {
                "rule_id": "R-1",
                "sentence_ids": ["S-00001"],
                "quote": "原文に無い言い換え",
                "issue": "主観的",
                "rationale": "ルールに反する",
                "severity": "major",
            },
            {
                "rule_id": "R-x",
                "sentence_ids": ["S-00001"],
                "quote": "優れている",
                "issue": "-",
                "rationale": "-",
            },
        ]
    }
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=chat(payload))
        async with OitomaClient(config(), api_key="k") as client:
            (found,) = await check_rules(
                client, [target], rules, prompt_lang="ja", output_language="日本語"
            )
        sent = json.loads(respx.calls[0].request.content)["messages"][1]["content"]
    assert "[R-1] 主観的な形容を避ける" in sent
    assert found.rule.rule_id == "R-1" and found.severity == "major"
    assert found.quote == "本手法は優れている。"  # 原文に無い引用は文の全文に置き換える


async def test_pipeline_adds_rule_findings() -> None:
    doc = make_doc(
        [
            (
                "4 実験",
                "experiments",
                [
                    [
                        "精度は 85% であった。",
                        "再現率は 80% であった。",
                        "F1 は 82％ と非常に優れていた。",
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
        if "ルールを守っているか" in system:
            return chat(
                {
                    "violations": [
                        {
                            "rule_id": "R-1",
                            "sentence_ids": ["S-00003"],
                            "quote": "非常に優れていた",
                            "issue": "主観的な表現",
                            "rationale": "規定に反する",
                            "severity": "major",
                        }
                    ]
                }
            )
        if "文章を改善" in system:
            return chat({"suggestions": ["F1 は 82% であった。"]})
        return chat({"issues": []})

    rules = [*BUILTIN_RULES, rule("R-1", "主観的な形容を避ける")]
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=respond)
        async with OitomaClient(config(), api_key="k") as client:
            result = await analyze_logic(
                doc,
                client,
                AnalysisSettings(with_flow_check=False, with_citation_check=False),
                rules=rules,
            )
    by_sub = {f.subcategory: f for f in result.findings}
    assert set(by_sub) == {"unit_consistency", "rule_violation"}
    unit = by_sub["unit_consistency"]
    assert (
        unit.category == "rule"
        and unit.confidence == "high"
        and unit.models.primary == "mechanical"
    )
    violation = by_sub["rule_violation"]
    assert violation.evidence[0].type == "rule" and violation.evidence[0].ref == "R-1"
    assert "規定.pdf p.3" in violation.evidence[0].excerpt
    assert violation.confidence == "low"


def test_group_targets_by_section_and_size() -> None:
    long = "長い段落の文である。" * 60
    doc = make_doc(
        [
            ("1 はじめに", "introduction", [["背景を述べる。"], ["目的を述べる。"]]),
            ("2 手法", "methods", [[long], [long], [long]]),
        ]
    )
    targets = select_targets(doc, min_chars=1)
    groups = group_targets(targets, max_tokens=800)
    sizes = [len(g) for g in groups]
    assert sizes[0] == 2  # はじめにの2段落は1回で判定する
    assert sum(sizes) == len(targets) and len(groups) >= 3  # 長い節は分ける
    assert all(len({t.paragraph.section_id for t in g}) == 1 for g in groups)


def test_mechanical_rules_merge_into_builtin() -> None:
    builtin = BUILTIN["abbreviation_definition"]
    extracted = rule(
        "R-abbr",
        "略語は初出時に定義すること",
        check_type="mechanical",
        checker="abbreviation_definition",
        source=RuleSource(doc="手引き.md"),
    )
    kept, updated = merge_rules([extracted], [None], {builtin.rule_id: builtin}, {})
    assert kept == []
    assert updated[builtin.rule_id].merged_sources == [RuleSource(doc="手引き.md")]


def test_read_docx_without_external_library(tmp_path: Path) -> None:
    import zipfile

    body = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        "<w:p><w:r><w:t>査読コメント1：</w:t></w:r><w:r><w:t>主張が強すぎる。</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>回答：表現を弱めました。</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    path = tmp_path / "memo.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", body)
    assert read_pages(path, "docx") == [
        (None, "査読コメント1：主張が強すぎる。\n回答：表現を弱めました。")
    ]
