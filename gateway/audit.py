"""監査ログ・承認キュー・結果のキャッシュ（SQLite、audit_log ボリューム）。

監査ログ（audit）は追記専用。更新・削除はトリガーで禁止する。
原稿の本文はここに保存しない（照合に使う情報はメモリ上だけに持つ。gateway/manuscripts.py）。
"""

import contextlib
import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from shared.schemas import Decision

DEFAULT_PATH = "/data/audit/audit.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    request_id TEXT NOT NULL,
    analysis_id TEXT,
    doc_id TEXT,
    kind TEXT,
    request TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE INDEX IF NOT EXISTS audit_by_analysis ON audit(analysis_id);

CREATE TABLE IF NOT EXISTS queue (
    request_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    request TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS cache (
    request_hash TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class AuditEntry(BaseModel):
    seq: int
    ts: str
    request_id: str
    analysis_id: str | None
    doc_id: str | None
    kind: str | None
    request: dict[str, Any] | str
    decision: str
    reason: str | None


class QueueItem(BaseModel):
    request_id: str
    analysis_id: str
    doc_id: str
    kind: str
    request: dict[str, Any]
    status: str
    created_at: str
    decided_at: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def request_hash(request: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(request, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def new_request_id() -> str:
    return f"R-{uuid.uuid4().hex[:12]}"


class AuditStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("GATEWAY_DB_PATH") or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------ 監査ログ（追記のみ）

    def record(
        self,
        *,
        request_id: str,
        analysis_id: str | None,
        doc_id: str | None,
        kind: str | None,
        request: dict[str, Any] | str,
        decision: Decision,
        reason: str | None = None,
    ) -> None:
        text = request if isinstance(request, str) else json.dumps(request, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit (ts, request_id, analysis_id, doc_id, kind, request, decision, "
                "reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), request_id, analysis_id, doc_id, kind, text[:2000], decision, reason),
            )

    def entries(self, *, analysis_id: str | None = None, limit: int = 500) -> list[AuditEntry]:
        query = "SELECT * FROM audit"
        params: list[Any] = []
        if analysis_id:
            query += " WHERE analysis_id = ?"
            params.append(analysis_id)
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            data = dict(r)
            with contextlib.suppress(ValueError):
                data["request"] = json.loads(data["request"])
            result.append(AuditEntry(**data))
        return result

    def sent_count(self, doc_id: str) -> int:
        """論文ごとに実際に学外へ送信した要求の数（件数制限に使う）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit WHERE doc_id = ? AND decision IN ('sent', 'approved')",
                (doc_id,),
            ).fetchone()
        return int(row[0])

    def external_count(self, analysis_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit WHERE analysis_id = ? AND decision IN ('sent', 'approved')",
                (analysis_id,),
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------ 承認キュー

    def enqueue(
        self, request_id: str, analysis_id: str, doc_id: str, request: dict[str, Any]
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO queue (request_id, analysis_id, doc_id, kind, request, request_hash, "
                "status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    request_id,
                    analysis_id,
                    doc_id,
                    request["kind"],
                    json.dumps(request, ensure_ascii=False),
                    request_hash(request),
                    _now(),
                ),
            )

    def pending_for(self, request: dict[str, Any]) -> str | None:
        """同じ内容の承認待ちの要求があれば、その ID を返す（二重に積まない）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT request_id FROM queue WHERE request_hash = ? AND status = 'pending'",
                (request_hash(request),),
            ).fetchone()
        return str(row[0]) if row else None

    def queue(self, status: str | None = "pending") -> list[QueueItem]:
        query = "SELECT * FROM queue"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [QueueItem(**{**dict(r), "request": json.loads(r["request"])}) for r in rows]

    def get_queue_item(self, request_id: str) -> QueueItem | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM queue WHERE request_id = ?", (request_id,)).fetchone()
        return QueueItem(**{**dict(row), "request": json.loads(row["request"])}) if row else None

    def decide(self, request_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE queue SET status = ?, decided_at = ? WHERE request_id = ?",
                (status, _now(), request_id),
            )

    # ------------------------------------------------------------ 結果のキャッシュ

    def cached(self, request: dict[str, Any]) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT response FROM cache WHERE request_hash = ?", (request_hash(request),)
            ).fetchone()
        return str(row[0]) if row else None

    def store_cache(self, request: dict[str, Any], response: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (request_hash, response, created_at) VALUES (?, ?, ?)",
                (request_hash(request), response, _now()),
            )
