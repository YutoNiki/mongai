"""監査ログ・承認キュー・結果のキャッシュ（SQLite、audit_log ボリューム）。

監査ログ（audit）は追記専用。更新・削除はトリガーで禁止する。
監査ログと承認キューには、要求を出した利用者の内部ユーザーID（owner_id）を記録し、一覧・承認は本人の分に限る
（認証の導入前の記録は owner_id が無く、利用者の一覧には出ない。共通デモの解析の記録は analysis_id で読む）。
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
    owner_id: str | None = None
    analysis_id: str | None
    doc_id: str | None
    kind: str | None
    request: dict[str, Any] | str
    decision: str
    reason: str | None


class QueueItem(BaseModel):
    request_id: str
    owner_id: str | None = None
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
            # 利用者の区別を加える前の表への列の追加（既存の行は変えない。追記専用のまま）
            for table in ("audit", "queue"):
                columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                if "owner_id" not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS audit_by_owner ON audit(owner_id)")

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
        owner_id: str | None,
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
                "INSERT INTO audit (ts, request_id, owner_id, analysis_id, doc_id, kind, request, "
                "decision, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _now(),
                    request_id,
                    owner_id,
                    analysis_id,
                    doc_id,
                    kind,
                    text[:2000],
                    decision,
                    reason,
                ),
            )

    @staticmethod
    def _scope(owner_id: str | None, analysis_id: str | None) -> tuple[str, list[Any]]:
        """利用者の記録（owner_id）か、共通デモの解析の記録（owner_id なし・analysis_id）の条件。"""
        if owner_id:
            if analysis_id:
                return "owner_id = ? AND analysis_id = ?", [owner_id, analysis_id]
            return "owner_id = ?", [owner_id]
        if analysis_id:
            return "owner_id IS NULL AND analysis_id = ?", [analysis_id]
        raise ValueError("owner_id か analysis_id のどちらかが必要")

    def entries(
        self, *, owner_id: str | None, analysis_id: str | None = None, limit: int = 500
    ) -> list[AuditEntry]:
        where, params = self._scope(owner_id, analysis_id)
        query = f"SELECT * FROM audit WHERE {where} ORDER BY seq DESC LIMIT ?"
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

    def external_count(self, analysis_id: str, *, owner_id: str | None) -> int:
        where, params = self._scope(owner_id, analysis_id)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM audit WHERE {where} AND decision IN ('sent', 'approved')",
                params,
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------ 承認キュー

    def enqueue(
        self,
        request_id: str,
        owner_id: str,
        analysis_id: str,
        doc_id: str,
        request: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO queue (request_id, owner_id, analysis_id, doc_id, kind, request, "
                "request_hash, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    request_id,
                    owner_id,
                    analysis_id,
                    doc_id,
                    request["kind"],
                    json.dumps(request, ensure_ascii=False),
                    request_hash(request),
                    _now(),
                ),
            )

    def pending_for(self, request: dict[str, Any], owner_id: str) -> str | None:
        """同じ利用者の、同じ内容の承認待ちの要求があれば、その ID を返す（二重に積まない）。

        別の利用者の要求とはまとめない（一方の承認で、もう一方の要求が送られないように）。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT request_id FROM queue WHERE request_hash = ? AND owner_id = ? "
                "AND status = 'pending'",
                (request_hash(request), owner_id),
            ).fetchone()
        return str(row[0]) if row else None

    def queue(self, *, owner_id: str, status: str | None = "pending") -> list[QueueItem]:
        query = "SELECT * FROM queue WHERE owner_id = ?"
        params: list[Any] = [owner_id]
        if status:
            query += " AND status = ?"
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
