"""データベース（SQLite）のスキーマと版の確認。

論文・解析・ルール・専門知識・利用者の表はすべて同じデータベースに置く。
版は `PRAGMA user_version` で管理する。新しいデータベースはこの版で作り、古い版のデータベースは
`make migrate`（core.store.migrate）で移行するまで使わない（所有者の無いデータを誤って見せないため）。

所有者を持つ表の `visibility`：
- private：所有者（owner_user_id）だけが読み書きできる
- public_demo：共通デモ。全員が読めて、誰も変更できない（所有者なし）
- builtin：組み込みのルール。全員が使い、利用者ごとの設定は rule_user_settings に持つ（所有者なし）
- quarantined：認証の導入前に作られ、所有者が分からないデータ。誰も読めない（所有者なし）
"""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

INSTRUCTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS instruction_profiles (
    profile_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(user_id),
    revision INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS instruction_profiles_by_owner ON instruction_profiles(owner_user_id);
CREATE TABLE IF NOT EXISTS instruction_drafts (
    draft_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(user_id),
    status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS instruction_drafts_by_owner ON instruction_drafts(owner_user_id);
CREATE TABLE IF NOT EXISTS analysis_requirement_results (
    analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
    requirement_id TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (analysis_id, requirement_id)
);
"""


def _owned(allowed: str) -> str:
    """所有者と公開範囲の列。private のときだけ所有者が必須で、それ以外は所有者を持たない。"""
    return f"""
    owner_user_id TEXT REFERENCES users(user_id),
    visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ({allowed})),
    CHECK ((visibility = 'private') = (owner_user_id IS NOT NULL))"""


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT,
    email TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS user_identities (
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    PRIMARY KEY (issuer, subject)
);
CREATE TABLE IF NOT EXISTS papers (
    doc_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL,
    content_sha256 TEXT,{_owned("'private', 'public_demo', 'quarantined'")}
);
CREATE INDEX IF NOT EXISTS papers_by_owner ON papers(owner_user_id, content_sha256);
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    title TEXT,
    lang TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    settings TEXT NOT NULL,
    progress TEXT NOT NULL DEFAULT '{{}}',
    warnings TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,{_owned("'private', 'public_demo', 'quarantined'")}
);
CREATE INDEX IF NOT EXISTS analyses_by_owner ON analyses(owner_user_id, created_at);
CREATE TABLE IF NOT EXISTS paragraph_structures (
    analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
    paragraph_id TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (analysis_id, paragraph_id)
);
CREATE TABLE IF NOT EXISTS findings (
    analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
    finding_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT NOT NULL,
    severity TEXT NOT NULL,
    confidence TEXT NOT NULL,
    section_id TEXT,
    page INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    dismiss_reason TEXT,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (analysis_id, finding_id)
);
CREATE INDEX IF NOT EXISTS findings_by_doc ON findings(doc_id);
CREATE TABLE IF NOT EXISTS api_usage (
    analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
    endpoint TEXT NOT NULL,
    model TEXT NOT NULL,
    role TEXT NOT NULL,
    calls INTEGER NOT NULL,
    requests INTEGER NOT NULL,
    retries INTEGER NOT NULL,
    failures INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    latency_s REAL NOT NULL,
    PRIMARY KEY (analysis_id, endpoint, model, role)
);
CREATE TABLE IF NOT EXISTS term_lookups (
    analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
    term TEXT NOT NULL,
    query TEXT,
    request_id TEXT,
    status TEXT NOT NULL,
    matched TEXT,
    description TEXT,
    source TEXT,
    reason TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (analysis_id, term)
);
CREATE INDEX IF NOT EXISTS term_lookups_by_request ON term_lookups(request_id);
-- 承認キューでの承認・却下の結果（要求ごと）。解析の途中で承認しても失われないよう、解析の記録とは分けて持つ。
-- 照会の記録に重ねるのは、決定した利用者と解析の所有者が同じときだけ
CREATE TABLE IF NOT EXISTS term_decisions (
    request_id TEXT PRIMARY KEY,
    owner_user_id TEXT,
    status TEXT NOT NULL,
    matched TEXT,
    description TEXT,
    source TEXT,
    decided_at TEXT NOT NULL
);
-- 日ごと（日本時間）の全体の利用回数（運用の確認用。一般の利用者の API には出さない）
CREATE TABLE IF NOT EXISTS api_usage_daily (
    day TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    PRIMARY KEY (day, endpoint, model)
);
CREATE TABLE IF NOT EXISTS api_usage_daily_by_user (
    day TEXT NOT NULL,
    user_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    PRIMARY KEY (day, user_id, endpoint, model)
);
CREATE TABLE IF NOT EXISTS rule_documents (
    document_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    rule_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,{_owned("'private', 'quarantined'")}
);
CREATE TABLE IF NOT EXISTS rules (
    rule_id TEXT PRIMARY KEY,
    document_id TEXT,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    embedding TEXT,
    updated_at TEXT NOT NULL,{_owned("'private', 'builtin', 'quarantined'")}
);
CREATE INDEX IF NOT EXISTS rules_by_document ON rules(document_id);
CREATE INDEX IF NOT EXISTS rules_by_owner ON rules(owner_user_id);
-- 組み込みのルールの、利用者ごとの設定（有効・無効と、その利用者の資料から統合された出典）
CREATE TABLE IF NOT EXISTS rule_user_settings (
    user_id TEXT NOT NULL REFERENCES users(user_id),
    rule_id TEXT NOT NULL,
    status TEXT,
    extra_sources TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, rule_id)
);
CREATE TABLE IF NOT EXISTS lab_documents (
    document_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    comment_count INTEGER NOT NULL DEFAULT 0,
    rule_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,{_owned("'private', 'quarantined'")}
);
CREATE INDEX IF NOT EXISTS lab_documents_by_owner ON lab_documents(owner_user_id);
CREATE TABLE IF NOT EXISTS lab_comments (
    comment_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    data TEXT NOT NULL,
    embedding TEXT,
    created_at TEXT NOT NULL,{_owned("'private', 'quarantined'")}
);
CREATE INDEX IF NOT EXISTS lab_comments_by_owner ON lab_comments(owner_user_id);
"""


class SchemaOutdatedError(RuntimeError):
    """データベースの版が、このコードの版と合わない（make migrate が必要）。"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def has_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()
    return int(row[0]) > 0


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA + INSTRUCTION_SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def ensure_schema(path: Path) -> None:
    """新しいデータベースなら今の版で作る。古い版なら SchemaOutdatedError（移行するまで使わない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        version = schema_version(conn)
        if version == SCHEMA_VERSION:
            return
        if version > SCHEMA_VERSION:
            raise SchemaOutdatedError(
                f"データベースの版（{version}）がこのコードの版（{SCHEMA_VERSION}）より新しい"
            )
        if has_tables(conn):
            raise SchemaOutdatedError(
                f"データベースの版（{version}）が古い。make migrate で移行してから起動する"
            )
        create_schema(conn)
    finally:
        conn.close()
