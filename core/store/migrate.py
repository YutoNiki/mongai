"""認証の導入前のデータベース（版0）を、所有者を持つ版（core.store.schema の SCHEMA_VERSION）に移行する。

使い方：core・worker を止めてから `make migrate`（コンテナ内で `python -m core.store.migrate`）。

- 最初に同じボリューム内へバックアップを作る（/data/db/backups/。学外にもホストの別の場所にも送らない）
- 共通デモの論文・解析だけを公開データ（public_demo）にし、ほかはすべて隔離（quarantined）する。
  最初にログインした利用者に割り当てることも、公開することもしない。持ち主は core.store.claim で決める
- デモの論文に付いたデモ以外の解析（誰かがデモの PDF をアップロードして解析したもの）も隔離する
- 組み込みのルールは既定の状態に戻す（今の有効・無効の設定は、誰が変えたか分からないので引き継がない）
- デモの指摘のステータスとデモの論文のファイル名・アップロード日時は、保存済みのデモの状態に戻す
- 1つのトランザクションで行い、検証に失敗したら元に戻す。版が上がらなければ core・worker は起動しない
"""

import argparse
import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from core.rules.models import BUILTIN_RULES
from core.sample import SAMPLE_ANALYSIS_ID, SAMPLE_DIR, SAMPLE_DOC_ID, _SampleAnalysis
from core.store.db import DEFAULT_DB_PATH
from core.store.schema import (
    INSTRUCTION_SCHEMA,
    SCHEMA,
    SCHEMA_VERSION,
    create_schema,
    has_tables,
    schema_version,
)

logger = logging.getLogger(__name__)

# 版0の表（存在するものだけを移す）と、名前の重なる索引
LEGACY_TABLES = (
    "papers",
    "documents",
    "analyses",
    "paragraph_structures",
    "findings",
    "api_usage",
    "term_lookups",
    "term_decisions",
    "api_usage_daily",
    "rule_documents",
    "rules",
    "lab_documents",
    "lab_comments",
)
LEGACY_INDEXES = (
    "findings_by_doc",
    "term_lookups_by_request",
    "rules_by_document",
    "lab_comments_by_lab",
)
OWNED_TABLES = ("papers", "analyses", "rule_documents", "rules", "lab_documents", "lab_comments")


class MigrationError(RuntimeError):
    pass


def backup(path: Path) -> Path:
    """SQLite のオンラインバックアップで、同じボリューム内に複製を作る。"""
    folder = path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"{path.stem}-{stamp}.sqlite3"
    src = sqlite3.connect(path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _upgrade_legacy_columns(conn: sqlite3.Connection, tables: set[str]) -> None:
    """版0の中の古い形（列の追加前）にそろえる（core.store.db が起動時に足していた列）。"""
    if "analyses" in tables and "progress" not in _columns(conn, "analyses"):
        conn.execute("ALTER TABLE analyses ADD COLUMN progress TEXT NOT NULL DEFAULT '{}'")
    if "term_lookups" in tables:
        for column in ("query", "matched"):
            if column not in _columns(conn, "term_lookups"):
                conn.execute(f"ALTER TABLE term_lookups ADD COLUMN {column} TEXT")


def _copy(conn: sqlite3.Connection, tables: set[str]) -> None:
    def has(name: str) -> bool:
        return f"legacy_{name}" in tables

    # デモの論文は、保存済みのファイル名・アップロード日時に戻す
    meta = _SampleAnalysis.model_validate_json((SAMPLE_DIR / "analysis.json").read_text())
    if has("papers"):
        conn.execute(
            "INSERT INTO papers (doc_id, filename, size, uploaded_at, owner_user_id, visibility) "
            "SELECT doc_id, filename, size, uploaded_at, NULL, "
            "CASE WHEN doc_id = ? THEN 'public_demo' ELSE 'quarantined' END FROM legacy_papers",
            (SAMPLE_DOC_ID,),
        )
        conn.execute(
            "UPDATE papers SET filename = ?, uploaded_at = ? WHERE doc_id = ?",
            (meta.filename, meta.uploaded_at, SAMPLE_DOC_ID),
        )
    if has("documents"):
        conn.execute("INSERT INTO documents SELECT * FROM legacy_documents")
    if has("analyses"):
        conn.execute(
            "INSERT INTO analyses (analysis_id, doc_id, kind, status, settings, progress, warnings, "
            "error, created_at, finished_at, owner_user_id, visibility) "
            "SELECT analysis_id, doc_id, kind, status, settings, progress, warnings, error, "
            "created_at, finished_at, NULL, "
            "CASE WHEN analysis_id = ? THEN 'public_demo' ELSE 'quarantined' END "
            "FROM legacy_analyses",
            (SAMPLE_ANALYSIS_ID,),
        )
    for name in ("paragraph_structures", "findings", "api_usage", "api_usage_daily"):
        if has(name):
            conn.execute(f"INSERT INTO {name} SELECT * FROM legacy_{name}")
    if has("findings"):
        conn.execute(
            "UPDATE findings SET status = 'open', dismiss_reason = NULL WHERE analysis_id = ?",
            (SAMPLE_ANALYSIS_ID,),
        )
    if has("term_lookups"):
        conn.execute(
            "INSERT INTO term_lookups (analysis_id, term, query, request_id, status, matched, "
            "description, source, reason, updated_at) SELECT analysis_id, term, query, request_id, "
            "status, matched, description, source, reason, updated_at FROM legacy_term_lookups"
        )
    if has("term_decisions"):
        conn.execute(
            "INSERT INTO term_decisions (request_id, owner_user_id, status, matched, description, "
            "source, decided_at) SELECT request_id, NULL, status, matched, description, source, "
            "decided_at FROM legacy_term_decisions"
        )
    if has("rule_documents"):
        conn.execute(
            "INSERT INTO rule_documents (document_id, filename, kind, status, rule_count, error, "
            "created_at, finished_at, owner_user_id, visibility) SELECT document_id, filename, kind, "
            "status, rule_count, error, created_at, finished_at, NULL, 'quarantined' "
            "FROM legacy_rule_documents"
        )
    builtin = {r.rule_id: r for r in BUILTIN_RULES}
    if has("rules"):
        placeholders = ",".join("?" * len(builtin))
        conn.execute(
            "INSERT INTO rules (rule_id, document_id, status, data, embedding, updated_at, "
            "owner_user_id, visibility) SELECT rule_id, document_id, status, data, embedding, "
            f"updated_at, NULL, 'quarantined' FROM legacy_rules WHERE rule_id NOT IN ({placeholders})",
            tuple(builtin),
        )
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for rule in builtin.values():
        # 組み込みのルールは既定の状態で入れ直す（統合された他人の出典・共有の有効・無効は消える）
        conn.execute(
            "INSERT INTO rules (rule_id, document_id, status, data, updated_at, owner_user_id, "
            "visibility) VALUES (?, NULL, ?, ?, ?, NULL, 'builtin')",
            (rule.rule_id, rule.status, rule.model_dump_json(), now),
        )
    if has("lab_documents"):
        conn.execute(
            "INSERT INTO lab_documents (document_id, filename, kind, status, comment_count, "
            "rule_count, error, created_at, finished_at, owner_user_id, visibility) "
            "SELECT document_id, filename, kind, status, comment_count, rule_count, error, "
            "created_at, finished_at, NULL, 'quarantined' FROM legacy_lab_documents"
        )
    if has("lab_comments"):
        conn.execute(
            "INSERT INTO lab_comments (comment_id, document_id, data, embedding, created_at, "
            "owner_user_id, visibility) SELECT comment_id, document_id, "
            "json_remove(data, '$.lab_id'), embedding, created_at, NULL, 'quarantined' "
            "FROM legacy_lab_comments"
        )


def _verify(conn: sqlite3.Connection, before: dict[str, int]) -> None:
    """移行後の件数（組み込みのルールは入れ直した数）と、公開範囲が正しいことを確かめる。"""
    for table, count in before.items():
        after = _count(conn, table)
        if after != count:
            raise MigrationError(f"{table} の件数が合わない（{count} → {after}）")
    for table in OWNED_TABLES:
        bad = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE visibility = 'private' AND owner_user_id IS NULL"
        ).fetchone()[0]
        if bad:
            raise MigrationError(f"{table} に所有者の無い個人のデータがある")
    demo = {
        "papers": ("doc_id", {SAMPLE_DOC_ID}),
        "analyses": ("analysis_id", {SAMPLE_ANALYSIS_ID}),
    }
    for table, (key, allowed) in demo.items():
        rows = conn.execute(f"SELECT {key} FROM {table} WHERE visibility = 'public_demo'")
        if {r[0] for r in rows} - allowed:
            raise MigrationError(f"{table} にデモ以外の公開データがある")
    for table in ("rule_documents", "lab_documents", "lab_comments"):
        public = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE visibility != 'quarantined'"
        ).fetchone()[0]
        if public:
            raise MigrationError(f"{table} に隔離されていない行がある")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise MigrationError("外部キーの整合性が取れない")


def migrate(path: Path) -> str:
    """移行して結果を返す。移行済み・新しいデータベースなら何もしない（新しいものは今の版で作る）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # トランザクションを自分で管理する
    try:
        version = schema_version(conn)
        if version == SCHEMA_VERSION:
            return f"移行済みです（版 {version}）"
        if version > SCHEMA_VERSION:
            raise MigrationError(f"データベースの版（{version}）がこのコードより新しい")
        if not has_tables(conn):
            create_schema(conn)
            return f"新しいデータベースを版 {SCHEMA_VERSION} で作りました"
        saved = backup(path)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if version == 1:
                for statement in INSTRUCTION_SCHEMA.split(";\n"):
                    if statement.strip():
                        conn.execute(statement)
                if conn.execute("PRAGMA foreign_key_check").fetchall():
                    raise MigrationError("外部キーの整合性が取れない")
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.execute("COMMIT")
                return f"版 {SCHEMA_VERSION} に移行しました（バックアップ：{saved}）。"
            tables = _tables(conn)
            _upgrade_legacy_columns(conn, tables)
            before = {t: _count(conn, t) for t in LEGACY_TABLES if t in tables}
            if "rules" in before:
                # 組み込みのルールは入れ直すので、移行後の件数は「組み込み以外＋組み込み」になる
                ids = tuple(r.rule_id for r in BUILTIN_RULES)
                custom = conn.execute(
                    f"SELECT COUNT(*) FROM rules WHERE rule_id NOT IN ({','.join('?' * len(ids))})",
                    ids,
                ).fetchone()[0]
                before["rules"] = int(custom) + len(ids)
            for index in LEGACY_INDEXES:
                conn.execute(f"DROP INDEX IF EXISTS {index}")
            for table in before:
                conn.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")
            for statement in (SCHEMA + INSTRUCTION_SCHEMA).split(";\n"):
                if statement.strip():
                    conn.execute(statement)
            _copy(conn, _tables(conn))
            for table in before:
                conn.execute(f"DROP TABLE legacy_{table}")
            _verify(conn, before)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return (
            f"版 {SCHEMA_VERSION} に移行しました（バックアップ：{saved}）。"
            "認証の導入前のデータは隔離したので、持ち主は make claim-legacy で決めてください"
        )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=None, help="データベースのパス（既定は MONGAI_DB_PATH）")
    args = parser.parse_args()
    path = Path(args.db or os.environ.get("MONGAI_DB_PATH") or DEFAULT_DB_PATH)
    print(migrate(path))


if __name__ == "__main__":
    main()
