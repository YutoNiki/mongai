"""SQLite による保存。

- 指摘の本体は JSON で保存し、絞り込みに使う項目（カテゴリ・重大度・ステータス等）は列にも持つ
- 指摘のステータス（対応済み・却下と理由）は列を正とする
- 1つの解析ごとに、おいとまAPIの利用回数（エンドポイント・モデル・役割別）を保存する
"""

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
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
"""


class FindingNotFoundError(KeyError):
    pass


class Progress(BaseModel):
    """解析の進捗。stage は ingest / structure / paragraph_check / flow_check / suggestion。"""

    stage: str | None = None
    done: int = 0
    total: int = 0


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

    def save_paper(self, doc_id: str, *, filename: str, size: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO papers (doc_id, filename, size, uploaded_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(doc_id) DO UPDATE SET filename=excluded.filename",
                (doc_id, filename, size, _now()),
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
