"""認証の導入前のデータベース（版0）の移行と、隔離したデータの引き取り（J.2 #29・#30）。"""

import json
import sqlite3
from pathlib import Path

import pytest

from core.rules.models import BUILTIN_RULES
from core.sample import SAMPLE_ANALYSIS_ID, SAMPLE_DOC_ID
from core.store import Store
from core.store.claim import ClaimError, claim
from core.store.migrate import MigrationError, migrate
from core.store.schema import SCHEMA_VERSION, SchemaOutdatedError, ensure_schema
from core.store.users import UserStore
from tests.unit.test_rules import rule
from tests.unit.test_store import finding

LEGACY = Path(__file__).resolve().parents[1] / "fixtures" / "legacy_schema_v0.sql"
NOW = "2026-10-01T00:00:00+00:00"


def legacy_db(path: Path) -> None:
    """版0のデータベースに合成データを入れる（デモ・デモの PDF をアップロードした人の解析・個人の論文など）。"""
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY.read_text())
    conn.executemany(
        "INSERT INTO papers VALUES (?, ?, ?, ?)",
        [
            (SAMPLE_DOC_ID, "上書きされたファイル名.pdf", 1, NOW),  # 再アップロードで上書きされた
            ("1111111111111111", "private.pdf", 2, NOW),
        ],
    )
    conn.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
        ("1111111111111111", "t", "ja", 1, NOW, "{}"),
    )
    for aid, doc in (
        (SAMPLE_ANALYSIS_ID, SAMPLE_DOC_ID),
        ("A-000000000001", SAMPLE_DOC_ID),  # 誰かがデモの PDF をアップロードして解析した
        ("A-000000000002", "1111111111111111"),
    ):
        conn.execute(
            "INSERT INTO analyses (analysis_id, doc_id, kind, status, settings, created_at) "
            "VALUES (?, ?, 'logic', 'completed', '{}', ?)",
            (aid, doc, NOW),
        )
    conn.execute(
        "INSERT INTO findings VALUES (?, 'F-0001', ?, 'logic', 'leap', 'major', 'low', NULL, 1, "
        "'dismissed', '誰かが却下した', ?, ?)",
        (
            SAMPLE_ANALYSIS_ID,
            SAMPLE_DOC_ID,
            finding("F-0001", doc_id=SAMPLE_DOC_ID).model_dump_json(),
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO term_lookups (analysis_id, term, request_id, status, updated_at) "
        "VALUES ('A-000000000002', '用語', 'R-1', 'pending', ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO term_decisions VALUES ('R-1', 'approved', NULL, NULL, NULL, ?)", (NOW,)
    )
    conn.execute(
        "INSERT INTO rule_documents (document_id, filename, kind, status, created_at) "
        "VALUES ('RD-000000000001', 'rules.md', 'markdown', 'completed', ?)",
        (NOW,),
    )
    builtin = BUILTIN_RULES[0].model_copy(update={"status": "disabled"})
    conn.executemany(
        "INSERT INTO rules (rule_id, document_id, status, data, updated_at) VALUES (?, ?, ?, ?, ?)",
        [
            (
                builtin.rule_id,
                None,
                "disabled",
                builtin.model_dump_json(),
                NOW,
            ),  # 共有で無効にされた
            (
                "R-1",
                "RD-000000000001",
                "approved",
                rule("R-1", "要旨に目的を書く", document_id="RD-000000000001").model_dump_json(),
                NOW,
            ),
        ],
    )
    conn.execute(
        "INSERT INTO lab_documents (document_id, lab_id, filename, kind, status, created_at) "
        "VALUES ('LD-000000000001', 'default', 'lab.md', 'markdown', 'completed', ?)",
        (NOW,),
    )
    comment = {
        "comment_id": "LC-1",
        "lab_id": "default",
        "document_id": "LD-000000000001",
        "comment": "指摘",
        "category": "other",
        "source": {"doc": "lab.md"},
    }
    conn.execute(
        "INSERT INTO lab_comments VALUES ('LC-1', 'default', 'LD-000000000001', ?, NULL, ?)",
        (json.dumps(comment, ensure_ascii=False), NOW),
    )
    conn.commit()
    conn.close()


def rows(path: Path, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(path)
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def test_legacy_database_is_not_used_before_migration(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    legacy_db(path)
    with pytest.raises(SchemaOutdatedError):
        ensure_schema(path)
    with pytest.raises(SchemaOutdatedError):
        Store(path)


def test_migration_quarantines_legacy_data(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    legacy_db(path)
    message = migrate(path)
    assert "移行しました" in message
    assert list((tmp_path / "backups").glob("db-*.sqlite3"))  # バックアップを作った
    assert rows(path, "PRAGMA user_version") == [(SCHEMA_VERSION,)]
    store = Store(path)
    # デモだけが公開データ。デモの PDF をアップロードした人の解析・個人の論文は隔離する
    assert rows(path, "SELECT doc_id, visibility FROM papers ORDER BY doc_id") == [
        ("1111111111111111", "quarantined"),
        (SAMPLE_DOC_ID, "public_demo"),
    ]
    assert {r[0]: r[1] for r in rows(path, "SELECT analysis_id, visibility FROM analyses")} == {
        SAMPLE_ANALYSIS_ID: "public_demo",
        "A-000000000001": "quarantined",
        "A-000000000002": "quarantined",
    }
    # 誰も隔離されたデータを読めない（ゲストも、新しく登録した利用者も）
    newcomer = UserStore(path).create_user()
    for viewer in (None, newcomer):
        assert [a.analysis_id for a in store.list_analyses(viewer)] == [SAMPLE_ANALYSIS_ID]
        assert store.get_paper_for("1111111111111111", viewer) is None
        assert store.get_analysis_for("A-000000000001", viewer) is None
    # デモは保存済みの状態に戻す（ファイル名・指摘のステータス）
    demo = store.get_paper(SAMPLE_DOC_ID)
    assert demo is not None and demo.filename != "上書きされたファイル名.pdf"
    assert [(f.status, f.dismiss_reason) for f in store.get_findings(SAMPLE_ANALYSIS_ID)] == [
        ("open", None)
    ]
    # 組み込みのルールは既定の状態に戻り、ほかのルール・資料・専門知識は隔離する
    from core.lab.store import LabStore
    from core.rules.store import RuleStore

    rules = RuleStore(path)
    assert rules.get_rule(newcomer, BUILTIN_RULES[0].rule_id).status == BUILTIN_RULES[0].status  # type: ignore[union-attr]
    assert rules.get_rule(newcomer, "R-1") is None and rules.list_documents(newcomer) == []
    assert LabStore(path).list_comments(newcomer) == []
    # 決定の記録も、どの解析にも重ねない
    assert rows(path, "SELECT owner_user_id FROM term_decisions") == [(None,)]
    # 移行済みなら何もしない
    assert "移行済み" in migrate(path)


def test_failed_migration_is_rolled_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """J.2 #30：途中で失敗したら元に戻り、版は上がらず、使えないまま（公開しない）。"""
    path = tmp_path / "db.sqlite3"
    legacy_db(path)
    import core.store.migrate as m

    def broken(conn: sqlite3.Connection, before: dict[str, int]) -> None:
        raise MigrationError("検証に失敗した")

    monkeypatch.setattr(m, "_verify", broken)
    with pytest.raises(MigrationError):
        migrate(path)
    assert rows(path, "PRAGMA user_version") == [(0,)]
    assert rows(path, "SELECT COUNT(*) FROM papers") == [(2,)]
    assert "visibility" not in {r[1] for r in rows(path, "PRAGMA table_info(papers)")}
    with pytest.raises(SchemaOutdatedError):
        Store(path)


def test_new_database_is_created_at_current_version(tmp_path: Path) -> None:
    path = tmp_path / "new.sqlite3"
    assert "新しいデータベース" in migrate(path)
    Store(path)


def test_claim_assigns_quarantined_data(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    legacy_db(path)
    migrate(path)
    owner = UserStore(path).create_user(display_name="元の利用者")
    counts = claim(
        path,
        owner,
        docs=["1111111111111111", SAMPLE_DOC_ID],
        rule_documents=["RD-000000000001"],
        lab_documents=["LD-000000000001"],
    )
    assert counts["papers"] == 1 and counts["analyses"] == 1  # デモは引き取れない
    store = Store(path)
    assert {a.analysis_id for a in store.list_analyses(owner)} == {
        "A-000000000002",
        SAMPLE_ANALYSIS_ID,
    }
    assert store.get_paper_for(SAMPLE_DOC_ID, None) is not None  # デモは公開のまま
    assert store.get_analysis_for("A-000000000001", owner) is None  # デモの PDF の解析は隔離のまま
    terms = store.get_term_lookups("A-000000000002")
    assert terms[0].status == "approved"  # 引き取った解析の決定は重ねる
    from core.lab.store import LabStore
    from core.rules.store import RuleStore

    assert [d.document_id for d in RuleStore(path).list_documents(owner)] == ["RD-000000000001"]
    assert [c.comment_id for c in LabStore(path).list_comments(owner)] == ["LC-1"]
    with pytest.raises(ClaimError):
        claim(path, "U-" + "0" * 32, docs=["1111111111111111"])
