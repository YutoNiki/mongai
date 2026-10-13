"""専門知識の保存（SQLite。論文のメタデータと同じデータベースの別のテーブル）。

すべての読み書きに研究室ID を付け、ほかの研究室の資料・指摘は返さない。
過去の指摘の埋め込み（類似箇所の検索に使う）も同じ行に JSON で持つ。
"""

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from core.lab.models import LabComment, LabDocument
from core.store.db import DEFAULT_DB_PATH

SCHEMA = """
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
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class LabStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("MONGAI_DB_PATH") or DEFAULT_DB_PATH)
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

    # ------------------------------------------------------------ 資料

    def create_document(self, lab_id: str, filename: str, kind: str) -> LabDocument:
        doc = LabDocument(
            document_id=f"LD-{uuid.uuid4().hex[:12]}",
            lab_id=lab_id,
            filename=filename,
            kind=kind,
            status="queued",
            created_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO lab_documents (document_id, lab_id, filename, kind, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (doc.document_id, lab_id, filename, doc.kind, doc.status, doc.created_at),
            )
        return doc

    def get_document(self, lab_id: str, document_id: str) -> LabDocument | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lab_documents WHERE document_id = ? AND lab_id = ?",
                (document_id, lab_id),
            ).fetchone()
        return LabDocument(**dict(row)) if row else None

    def find_document(self, document_id: str) -> LabDocument | None:
        """ワーカー用：ID だけで資料を探す（研究室ID は資料の記録から決まる）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lab_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return LabDocument(**dict(row)) if row else None

    def list_documents(self, lab_id: str) -> list[LabDocument]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lab_documents WHERE lab_id = ? ORDER BY created_at DESC, rowid DESC",
                (lab_id,),
            ).fetchall()
        return [LabDocument(**dict(r)) for r in rows]

    def set_document_status(
        self, document_id: str, status: str, *, error: str | None = None, rule_count: int = 0
    ) -> None:
        finished = _now() if status in ("completed", "failed") else None
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM lab_comments WHERE document_id = ?", (document_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE lab_documents SET status = ?, error = ?, comment_count = ?, rule_count = ?, "
                "finished_at = ? WHERE document_id = ?",
                (status, error, count, rule_count, finished, document_id),
            )

    def delete_document(self, lab_id: str, document_id: str) -> bool:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM lab_comments WHERE document_id = ? AND lab_id = ?",
                (document_id, lab_id),
            )
            cursor = conn.execute(
                "DELETE FROM lab_documents WHERE document_id = ? AND lab_id = ?",
                (document_id, lab_id),
            )
        return cursor.rowcount > 0

    def recover_interrupted(self) -> list[str]:
        """ワーカーの再起動で中断された取り込みを失敗にし、順番待ちの資料の ID を返す（積み直す）。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE lab_documents SET status = 'failed', error = ?, finished_at = ? "
                "WHERE status = 'running'",
                ("ワーカーの再起動で中断されました。資料を登録し直してください", _now()),
            )
            rows = conn.execute(
                "SELECT document_id FROM lab_documents WHERE status = 'queued'"
            ).fetchall()
        return [r["document_id"] for r in rows]

    # ------------------------------------------------------------ 過去の指摘

    def add_comments(
        self, comments: list[LabComment], embeddings: list[list[float] | None]
    ) -> None:
        with self._connect() as conn:
            for comment, vector in zip(comments, embeddings, strict=True):
                stamped = comment.model_copy(update={"created_at": _now()})
                conn.execute(
                    "INSERT INTO lab_comments "
                    "(comment_id, lab_id, document_id, data, embedding, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        comment.comment_id,
                        comment.lab_id,
                        comment.document_id,
                        stamped.model_dump_json(),
                        json.dumps(vector) if vector is not None else None,
                        stamped.created_at,
                    ),
                )

    def list_comments(self, lab_id: str) -> list[LabComment]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM lab_comments WHERE lab_id = ? ORDER BY rowid", (lab_id,)
            ).fetchall()
        return [LabComment.model_validate_json(r["data"]) for r in rows]

    def comment_vectors(self, lab_id: str) -> dict[str, list[float]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT comment_id, embedding FROM lab_comments "
                "WHERE lab_id = ? AND embedding IS NOT NULL",
                (lab_id,),
            ).fetchall()
        return {r["comment_id"]: json.loads(r["embedding"]) for r in rows}

    def delete_comment(self, lab_id: str, comment_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT document_id FROM lab_comments WHERE comment_id = ? AND lab_id = ?",
                (comment_id, lab_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM lab_comments WHERE comment_id = ?", (comment_id,))
            conn.execute(
                "UPDATE lab_documents SET comment_count = comment_count - 1 WHERE document_id = ?",
                (row["document_id"],),
            )
        return True
