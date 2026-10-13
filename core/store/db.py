"""SQLite による保存。

- 指摘の本体は JSON で保存し、絞り込みに使う項目（カテゴリ・重大度・ステータス等）は列にも持つ
- 指摘のステータス（対応済み・却下と理由）は列を正とする
- 1つの解析ごとに、おいとまAPIの利用回数（エンドポイント・モデル・役割別）を保存する
- 日ごと（日本時間）の利用回数は解析とは別に持つ（論文を削除しても、その日の回数は減らさない）
"""

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from core.analyze.models import Finding, ParagraphStructure
from core.ingest.models import Document
from core.llm.usage import UsageRow

DEFAULT_DB_PATH = "/data/db/mongai.sqlite3"

AnalysisStatus = Literal["queued", "running", "completed", "failed"]

SCHEMA = """
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
    request_id TEXT,
    status TEXT NOT NULL,
    description TEXT,
    source TEXT,
    reason TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (analysis_id, term)
);
CREATE INDEX IF NOT EXISTS term_lookups_by_request ON term_lookups(request_id);
CREATE TABLE IF NOT EXISTS api_usage_daily (
    day TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    PRIMARY KEY (day, endpoint, model)
);
"""

JST = timezone(timedelta(hours=9))


class FindingNotFoundError(KeyError):
    pass


class PaperBusyError(RuntimeError):
    """解析が順番待ち・実行中の論文は削除できない。"""


class DailyUsage(BaseModel):
    day: str  # 日本時間の日付（YYYY-MM-DD）
    requests: int = 0  # HTTP リクエストの回数（再試行を含む）
    prompt_tokens: int = 0
    completion_tokens: int = 0


def today_jst() -> str:
    return datetime.now(JST).date().isoformat()


class Progress(BaseModel):
    """解析の進捗。stage は ingest / structure / paragraph_check / flow_check / suggestion。"""

    stage: str | None = None
    done: int = 0
    total: int = 0


TermStatus = Literal["pending", "approved", "rejected", "found", "blocked", "failed"]


class TermLookupRecord(BaseModel):
    """解析で照会した未知の用語（gateway の承認制の term_lookup）と、その結果。

    status：pending 承認待ち / approved 承認して送信した / rejected 却下した / found 照会済み（キャッシュ）/
    blocked gateway が拒否した / failed 検索に失敗した。description は学外の辞書の説明（外部データ）。
    """

    term: str
    request_id: str | None = None
    status: TermStatus
    description: str | None = None
    source: str | None = None
    reason: str | None = None
    updated_at: str = ""


class PaperRecord(BaseModel):
    doc_id: str
    filename: str
    size: int
    uploaded_at: str


class AnalysisRecord(BaseModel):
    analysis_id: str
    doc_id: str
    kind: str
    status: AnalysisStatus
    settings: dict[str, Any]
    progress: Progress = Progress()
    warnings: list[str]
    error: str | None
    created_at: str
    finished_at: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("MONGAI_DB_PATH") or DEFAULT_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(analyses)")}
            if "progress" not in columns:  # M3 で作ったデータベースへの列の追加
                conn.execute("ALTER TABLE analyses ADD COLUMN progress TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------ アップロードされた論文

    def save_paper(
        self, doc_id: str, *, filename: str, size: int, uploaded_at: str | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO papers (doc_id, filename, size, uploaded_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(doc_id) DO UPDATE SET filename=excluded.filename",
                (doc_id, filename, size, uploaded_at or _now()),
            )

    def get_paper(self, doc_id: str) -> PaperRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM papers WHERE doc_id = ?", (doc_id,)).fetchone()
        return PaperRecord(**dict(row)) if row else None

    # ------------------------------------------------------------ 文書

    def save_document(self, doc: Document) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO documents (doc_id, title, lang, page_count, created_at, data) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(doc_id) DO UPDATE SET title=excluded.title, data=excluded.data",
                (doc.doc_id, doc.title, doc.lang, doc.page_count, _now(), doc.model_dump_json()),
            )

    def get_document(self, doc_id: str) -> Document | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        return Document.model_validate_json(row["data"]) if row else None

    # ------------------------------------------------------------ 解析

    def create_analysis(
        self,
        doc_id: str,
        *,
        kind: str,
        settings: BaseModel,
        status: AnalysisStatus = "running",
    ) -> str:
        analysis_id = f"A-{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO analyses (analysis_id, doc_id, kind, status, settings, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (analysis_id, doc_id, kind, status, settings.model_dump_json(), _now()),
            )
        return analysis_id

    def import_analysis(
        self, record: AnalysisRecord, findings: list[Finding], api_usage: list[UsageRow]
    ) -> None:
        """保存済みの解析結果（ID・日時を含む）をそのまま取り込む（例の解析に使う）。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO analyses (analysis_id, doc_id, kind, status, settings, progress, "
                "warnings, error, created_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.analysis_id,
                    record.doc_id,
                    record.kind,
                    record.status,
                    json.dumps(record.settings, ensure_ascii=False),
                    record.progress.model_dump_json(),
                    json.dumps(record.warnings, ensure_ascii=False),
                    record.error,
                    record.created_at,
                    record.finished_at,
                ),
            )
            self._save_usage(conn, record.analysis_id, api_usage)
        self.save_findings(record.analysis_id, findings)

    def update_settings(self, analysis_id: str, values: dict[str, Any]) -> None:
        """解析の設定に値を足す（解析の開始時に使ったルールセットの記録など）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT settings FROM analyses WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
            if row is None:
                return
            settings = {**json.loads(row["settings"]), **values}
            conn.execute(
                "UPDATE analyses SET settings = ? WHERE analysis_id = ?",
                (json.dumps(settings, ensure_ascii=False), analysis_id),
            )

    def set_status(self, analysis_id: str, status: AnalysisStatus) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE analyses SET status = ? WHERE analysis_id = ?", (status, analysis_id)
            )

    def set_progress(self, analysis_id: str, stage: str, done: int, total: int) -> None:
        progress = Progress(stage=stage, done=done, total=total).model_dump_json()
        with self._connect() as conn:
            conn.execute(
                "UPDATE analyses SET progress = ? WHERE analysis_id = ?", (progress, analysis_id)
            )

    def recover_interrupted(self) -> tuple[list[str], list[str]]:
        """ワーカーの起動時に呼ぶ。実行中だった解析を失敗にし、順番待ちの解析の ID を返す。

        戻り値：(失敗にした解析, 順番待ちの解析)
        """
        with self._connect() as conn:
            running = [
                r["analysis_id"]
                for r in conn.execute("SELECT analysis_id FROM analyses WHERE status = 'running'")
            ]
            queued = [
                r["analysis_id"]
                for r in conn.execute(
                    "SELECT analysis_id FROM analyses WHERE status = 'queued' ORDER BY created_at"
                )
            ]
            conn.execute(
                "UPDATE analyses SET status = 'failed', finished_at = ?, "
                "error = 'ワーカーの再起動により解析が中断されました。もう一度解析してください' "
                "WHERE status = 'running'",
                (_now(),),
            )
        return running, queued

    def delete_paper(self, doc_id: str) -> list[str]:
        """論文と、その論文のすべての解析結果を削除し、削除した解析の ID を返す。

        解析が順番待ち・実行中なら PaperBusyError。PDF ファイルは呼び出し側で消す。
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")  # 確認から削除までの間に解析が始まらないようにする
            rows = conn.execute(
                "SELECT analysis_id, status FROM analyses WHERE doc_id = ?", (doc_id,)
            ).fetchall()
            if any(r["status"] in ("queued", "running") for r in rows):
                raise PaperBusyError(doc_id)
            ids = [r["analysis_id"] for r in rows]
            for table in ("findings", "paragraph_structures", "api_usage", "term_lookups"):
                conn.executemany(
                    f"DELETE FROM {table} WHERE analysis_id = ?",  # 表名は固定の4つだけ
                    [(i,) for i in ids],
                )
            conn.execute("DELETE FROM analyses WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM papers WHERE doc_id = ?", (doc_id,))
        return ids

    def list_analyses(self, limit: int = 100) -> list[AnalysisRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM analyses ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._analysis(r) for r in rows]

    def find_previous(self, analysis_id: str) -> AnalysisRecord | None:
        """同じ論文（同じ ID、または同じタイトル・ファイル名）の、これより前の完了した解析。"""
        current = self.get_analysis(analysis_id)
        if current is None:
            return None
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT a.*, d.title AS title, p.filename AS filename FROM analyses a "
                "LEFT JOIN documents d ON d.doc_id = a.doc_id "
                "LEFT JOIN papers p ON p.doc_id = a.doc_id "
                "WHERE a.status = 'completed' AND a.analysis_id != ? AND a.created_at <= ? "
                "ORDER BY a.created_at DESC, a.rowid DESC",
                (analysis_id, current.created_at),
            ).fetchall()
            mine = conn.execute(
                "SELECT d.title AS title, p.filename AS filename FROM analyses a "
                "LEFT JOIN documents d ON d.doc_id = a.doc_id "
                "LEFT JOIN papers p ON p.doc_id = a.doc_id WHERE a.analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        for row in rows:
            same_doc = row["doc_id"] == current.doc_id
            same_title = bool(mine["title"]) and row["title"] == mine["title"]
            same_file = bool(mine["filename"]) and row["filename"] == mine["filename"]
            if same_doc or same_title or same_file:
                return self._analysis(row)
        return None

    @staticmethod
    def _analysis(row: sqlite3.Row) -> AnalysisRecord:
        return AnalysisRecord(
            analysis_id=row["analysis_id"],
            doc_id=row["doc_id"],
            kind=row["kind"],
            status=row["status"],
            settings=json.loads(row["settings"]),
            progress=Progress.model_validate_json(row["progress"] or "{}"),
            warnings=json.loads(row["warnings"]),
            error=row["error"],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
        )

    def finish_analysis(
        self,
        analysis_id: str,
        *,
        status: AnalysisStatus,
        warnings: list[str] | None = None,
        error: str | None = None,
        api_usage: list[UsageRow] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE analyses SET status = ?, warnings = ?, error = ?, finished_at = ? "
                "WHERE analysis_id = ?",
                (
                    status,
                    json.dumps(warnings or [], ensure_ascii=False),
                    error,
                    _now(),
                    analysis_id,
                ),
            )
            if api_usage:
                self._save_usage(conn, analysis_id, api_usage)

    def get_analysis(self, analysis_id: str) -> AnalysisRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
        return self._analysis(row) if row is not None else None

    def save_structures(self, analysis_id: str, structures: list[ParagraphStructure]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO paragraph_structures (analysis_id, paragraph_id, data) "
                "VALUES (?, ?, ?)",
                [(analysis_id, s.paragraph_id, s.model_dump_json()) for s in structures],
            )

    def get_structures(self, analysis_id: str) -> list[ParagraphStructure]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM paragraph_structures WHERE analysis_id = ? ORDER BY paragraph_id",
                (analysis_id,),
            ).fetchall()
        return [ParagraphStructure.model_validate_json(r["data"]) for r in rows]

    # ------------------------------------------------------------ 未知の用語の照会

    def save_term_lookups(self, analysis_id: str, records: list[TermLookupRecord]) -> None:
        now = _now()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO term_lookups (analysis_id, term, request_id, status, "
                "description, source, reason, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        analysis_id,
                        r.term,
                        r.request_id,
                        r.status,
                        r.description,
                        r.source,
                        r.reason,
                        now,
                    )
                    for r in records
                ],
            )

    def get_term_lookups(self, analysis_id: str) -> list[TermLookupRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT term, request_id, status, description, source, reason, updated_at "
                "FROM term_lookups WHERE analysis_id = ? ORDER BY rowid",
                (analysis_id,),
            ).fetchall()
        return [TermLookupRecord(**dict(r)) for r in rows]

    def decide_term_lookup(
        self,
        request_id: str,
        status: TermStatus,
        *,
        description: str | None = None,
        source: str | None = None,
    ) -> int:
        """承認キューで承認・却下した結果を、その要求を積んだすべての解析に反映し、更新した数を返す。"""
        with self._connect() as conn:
            return conn.execute(
                "UPDATE term_lookups SET status = ?, description = ?, source = ?, updated_at = ? "
                "WHERE request_id = ?",
                (status, description, source, _now(), request_id),
            ).rowcount

    # ------------------------------------------------------------ 指摘

    def save_findings(self, analysis_id: str, findings: list[Finding]) -> None:
        """指摘を保存する（同じ ID は内容を更新し、利用者が変えたステータスは保つ）。"""
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO findings (analysis_id, finding_id, doc_id, category, subcategory, "
                "severity, confidence, section_id, page, status, dismiss_reason, data, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(analysis_id, finding_id) DO UPDATE SET "
                "severity=excluded.severity, confidence=excluded.confidence, "
                "data=excluded.data, updated_at=excluded.updated_at",
                [
                    (
                        analysis_id,
                        f.finding_id,
                        f.doc_id,
                        f.category,
                        f.subcategory,
                        f.severity,
                        f.confidence,
                        f.section_id,
                        f.page,
                        f.status,
                        f.dismiss_reason,
                        f.model_dump_json(),
                        _now(),
                    )
                    for f in findings
                ],
            )

    def get_findings(
        self,
        analysis_id: str,
        *,
        category: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        section_id: str | None = None,
        confidence: str | None = None,
    ) -> list[Finding]:
        query = "SELECT data, status, dismiss_reason FROM findings WHERE analysis_id = ?"
        params: list[Any] = [analysis_id]
        filters = (
            ("category", category),
            ("severity", severity),
            ("status", status),
            ("section_id", section_id),
            ("confidence", confidence),
        )
        for column, value in filters:
            if value is not None:
                query += f" AND {column} = ?"
                params.append(value)
        query += " ORDER BY finding_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            Finding.model_validate_json(r["data"]).model_copy(
                update={"status": r["status"], "dismiss_reason": r["dismiss_reason"]}
            )
            for r in rows
        ]

    def set_finding_status(
        self,
        analysis_id: str,
        finding_id: str,
        status: Literal["open", "accepted", "dismissed"],
        reason: str | None = None,
    ) -> None:
        """指摘を「対応済み」「却下（理由）」にする。却下以外では理由を消す。"""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE findings SET status = ?, dismiss_reason = ?, updated_at = ? "
                "WHERE analysis_id = ? AND finding_id = ?",
                (
                    status,
                    reason if status == "dismissed" else None,
                    _now(),
                    analysis_id,
                    finding_id,
                ),
            )
            if cursor.rowcount == 0:
                raise FindingNotFoundError(f"{analysis_id} / {finding_id}")

    # ------------------------------------------------------------ APIの利用回数

    @staticmethod
    def _save_usage(conn: sqlite3.Connection, analysis_id: str, rows: list[UsageRow]) -> None:
        conn.executemany(
            "INSERT OR REPLACE INTO api_usage (analysis_id, endpoint, model, role, calls, "
            "requests, retries, failures, prompt_tokens, completion_tokens, latency_s) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    analysis_id,
                    r.endpoint,
                    r.model,
                    r.role,
                    r.calls,
                    r.requests,
                    r.retries,
                    r.failures,
                    r.prompt_tokens,
                    r.completion_tokens,
                    r.latency_s,
                )
                for r in rows
            ],
        )

    def add_daily_usage(self, day: str, rows: list[UsageRow]) -> None:
        """その日の利用回数に rows（前回からの増分）を足す。"""
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO api_usage_daily (day, endpoint, model, requests, prompt_tokens, "
                "completion_tokens) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(day, endpoint, model) DO UPDATE SET "
                "requests = requests + excluded.requests, "
                "prompt_tokens = prompt_tokens + excluded.prompt_tokens, "
                "completion_tokens = completion_tokens + excluded.completion_tokens",
                [
                    (day, r.endpoint, r.model, r.requests, r.prompt_tokens, r.completion_tokens)
                    for r in rows
                    if r.requests or r.prompt_tokens or r.completion_tokens
                ],
            )

    def get_daily_usage(self, day: str) -> DailyUsage:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(requests), 0) AS requests, "
                "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens), 0) AS completion_tokens "
                "FROM api_usage_daily WHERE day = ?",
                (day,),
            ).fetchone()
        return DailyUsage(day=day, **dict(row))

    def get_api_usage(self, analysis_id: str) -> list[UsageRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM api_usage WHERE analysis_id = ? ORDER BY endpoint, model, role",
                (analysis_id,),
            ).fetchall()
        result = []
        for r in rows:
            data = dict(r)
            data.pop("analysis_id")
            result.append(UsageRow(**data))
        return result
