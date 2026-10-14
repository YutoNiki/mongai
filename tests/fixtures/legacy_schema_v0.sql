-- 認証の導入前（版0）のスキーマ（upstream/main 9b80146 の core/store/db.py・core/rules/store.py・core/lab/store.py）。
-- 移行（core.store.migrate）のテストに使う。合成データのためのもので、実データは含まない。

CREATE TABLE IF NOT EXISTS papers (
    doc_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL
);
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
    progress TEXT NOT NULL DEFAULT '{}',
    warnings TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
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
-- 承認キューでの承認・却下の結果（要求ごと）。解析の途中で承認しても失われないよう、解析の記録とは分けて持つ
CREATE TABLE IF NOT EXISTS term_decisions (
    request_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    matched TEXT,
    description TEXT,
    source TEXT,
    decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_usage_daily (
    day TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    PRIMARY KEY (day, endpoint, model)
);

CREATE TABLE IF NOT EXISTS rule_documents (
    document_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    rule_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS rules (
    rule_id TEXT PRIMARY KEY,
    document_id TEXT,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    embedding TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rules_by_document ON rules(document_id);

CREATE TABLE IF NOT EXISTS lab_documents (
    document_id TEXT PRIMARY KEY,
    lab_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    comment_count INTEGER NOT NULL DEFAULT 0,
    rule_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS lab_comments (
    comment_id TEXT PRIMARY KEY,
    lab_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    data TEXT NOT NULL,
    embedding TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lab_comments_by_lab ON lab_comments(lab_id);
