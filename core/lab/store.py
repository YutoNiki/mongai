"""専門知識の保存（SQLite。論文のメタデータと同じデータベースの別のテーブル）。

資料と過去の指摘は利用者個人のもので、すべての読み書きに所有者（owner_user_id）を付け、
ほかの利用者の資料・指摘は返さない。
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
from core.store.schema import connect, ensure_schema


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class LabStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("MONGAI_DB_PATH") or DEFAULT_DB_PATH)
        ensure_schema(self.path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------ 資料

    def create_document(self, owner_user_id: str, filename: str, kind: str) -> LabDocument:
        doc = LabDocument(
            document_id=f"LD-{uuid.uuid4().hex[:12]}",
            filename=filename,
            kind=kind,
            status="queued",
            created_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO lab_documents (document_id, filename, kind, status, created_at, "
                "owner_user_id, visibility) VALUES (?, ?, ?, ?, ?, ?, 'private')",
                (doc.document_id, filename, doc.kind, doc.status, doc.created_at, owner_user_id),
            )
        return doc

    @staticmethod
    def _document(row: sqlite3.Row) -> LabDocument:
        data = dict(row)
        data.pop("owner_user_id", None)
        data.pop("visibility", None)
        return LabDocument(**data)

    def get_document(self, owner_user_id: str, document_id: str) -> LabDocument | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lab_documents WHERE document_id = ? AND owner_user_id = ? "
                "AND visibility = 'private'",
                (document_id, owner_user_id),
            ).fetchone()
        return self._document(row) if row else None

    def find_document(self, document_id: str) -> tuple[LabDocument, str] | None:
        """ワーカー用：ID だけで資料を探し、資料と所有者を返す（所有者のいない資料は None）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lab_documents WHERE document_id = ? AND visibility = 'private'",
                (document_id,),
            ).fetchone()
        return (self._document(row), str(row["owner_user_id"])) if row else None

    def list_documents(self, owner_user_id: str) -> list[LabDocument]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lab_documents WHERE owner_user_id = ? AND visibility = 'private' "
                "ORDER BY created_at DESC, rowid DESC",
                (owner_user_id,),
            ).fetchall()
        return [self._document(r) for r in rows]

    def set_document_status(
        self,
        document_id: str,
        status: str,
        *,
        error: str | None = None,
        rule_count: int = 0,
        only_if_queued: bool = False,
    ) -> None:
        finished = _now() if status in ("completed", "failed") else None
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM lab_comments WHERE document_id = ?", (document_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE lab_documents SET status = ?, error = ?, comment_count = ?, rule_count = ?, "
                "finished_at = ? WHERE document_id = ?"
                + (" AND status = 'queued'" if only_if_queued else ""),
                (status, error, count, rule_count, finished, document_id),
            )

    def delete_document(self, owner_user_id: str, document_id: str) -> bool:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM lab_comments WHERE document_id = ? AND owner_user_id = ?",
                (document_id, owner_user_id),
            )
            cursor = conn.execute(
                "DELETE FROM lab_documents WHERE document_id = ? AND owner_user_id = ?",
                (document_id, owner_user_id),
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
                "SELECT document_id FROM lab_documents WHERE status = 'queued' "
                "AND visibility = 'private'"
            ).fetchall()
        return [r["document_id"] for r in rows]

    # ------------------------------------------------------------ 過去の指摘

    def add_comments(
        self,
        owner_user_id: str,
        comments: list[LabComment],
        embeddings: list[list[float] | None],
    ) -> None:
        with self._connect() as conn:
            for comment, vector in zip(comments, embeddings, strict=True):
                stamped = comment.model_copy(update={"created_at": _now()})
                conn.execute(
                    "INSERT INTO lab_comments (comment_id, document_id, data, embedding, "
                    "created_at, owner_user_id, visibility) VALUES (?, ?, ?, ?, ?, ?, 'private')",
                    (
                        comment.comment_id,
                        comment.document_id,
                        stamped.model_dump_json(),
                        json.dumps(vector) if vector is not None else None,
                        stamped.created_at,
                        owner_user_id,
                    ),
                )

    def list_comments(self, owner_user_id: str) -> list[LabComment]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM lab_comments WHERE owner_user_id = ? AND visibility = 'private' "
                "ORDER BY rowid",
                (owner_user_id,),
            ).fetchall()
        return [LabComment.model_validate_json(r["data"]) for r in rows]

    def comment_vectors(self, owner_user_id: str) -> dict[str, list[float]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT comment_id, embedding FROM lab_comments WHERE owner_user_id = ? "
                "AND visibility = 'private' AND embedding IS NOT NULL",
                (owner_user_id,),
            ).fetchall()
        return {r["comment_id"]: json.loads(r["embedding"]) for r in rows}

    def delete_comment(self, owner_user_id: str, comment_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT document_id FROM lab_comments WHERE comment_id = ? AND owner_user_id = ? "
                "AND visibility = 'private'",
                (comment_id, owner_user_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM lab_comments WHERE comment_id = ?", (comment_id,))
            conn.execute(
                "UPDATE lab_documents SET comment_count = comment_count - 1 WHERE document_id = ?",
                (row["document_id"],),
            )
        return True
