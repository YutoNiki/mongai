"""ルールとルール資料の保存（SQLite。論文のメタデータと同じデータベースの別のテーブル）。

ルールの埋め込み（重複の統合・ルールの検索に使う）も同じ行に JSON で持つ。ルールは多くても数百件なので、
ベクタDB は使わず全件と比べる（TODO.md の決定事項）。
"""

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from core.rules.models import BUILTIN_RULES, Rule, RuleDocument, RuleStatus, RuleUpdate
from core.store.db import DEFAULT_DB_PATH

SCHEMA = """
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
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RuleNotFoundError(KeyError):
    pass


class RuleStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("MONGAI_DB_PATH") or DEFAULT_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # 組み込みのルールは無ければ入れる（利用者が無効にした状態は残す）
            for rule in BUILTIN_RULES:
                conn.execute(
                    "INSERT OR IGNORE INTO rules (rule_id, document_id, status, data, updated_at) "
                    "VALUES (?, NULL, ?, ?, ?)",
                    (rule.rule_id, rule.status, rule.model_dump_json(), _now()),
                )

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

    # ------------------------------------------------------------ ルール資料

    def create_document(self, filename: str, kind: str) -> RuleDocument:
        doc = RuleDocument(
            document_id=f"RD-{uuid.uuid4().hex[:12]}",
            filename=filename,
            kind=kind,
            status="queued",
            created_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO rule_documents (document_id, filename, kind, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (doc.document_id, doc.filename, doc.kind, doc.status, doc.created_at),
            )
        return doc

    def get_document(self, document_id: str) -> RuleDocument | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM rule_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return RuleDocument(**dict(row)) if row else None

    def list_documents(self) -> list[RuleDocument]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM rule_documents ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [RuleDocument(**dict(r)) for r in rows]

    def set_document_status(
        self, document_id: str, status: str, *, error: str | None = None
    ) -> None:
        finished = _now() if status in ("completed", "failed") else None
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM rules WHERE document_id = ?", (document_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE rule_documents SET status = ?, error = ?, rule_count = ?, finished_at = ? "
                "WHERE document_id = ?",
                (status, error, count, finished, document_id),
            )

    def delete_document(self, document_id: str) -> bool:
        """資料と、そこから抽出したルールを消す。資料が無ければ False。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM rules WHERE document_id = ?", (document_id,))
            cursor = conn.execute(
                "DELETE FROM rule_documents WHERE document_id = ?", (document_id,)
            )
        return cursor.rowcount > 0

    def recover_interrupted(self) -> list[str]:
        """ワーカーの再起動で中断された抽出を失敗にし、順番待ちの資料の ID を返す（積み直す）。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE rule_documents SET status = 'failed', error = ?, finished_at = ? "
                "WHERE status = 'running'",
                ("ワーカーの再起動で中断されました。資料を登録し直してください", _now()),
            )
            rows = conn.execute(
                "SELECT document_id FROM rule_documents WHERE status = 'queued'"
            ).fetchall()
        return [r["document_id"] for r in rows]

    # ------------------------------------------------------------ ルール

    def add_rules(self, rules: list[Rule], embeddings: list[list[float] | None]) -> None:
        with self._connect() as conn:
            for rule, vector in zip(rules, embeddings, strict=True):
                stamped = rule.model_copy(update={"updated_at": _now()})
                conn.execute(
                    "INSERT INTO rules (rule_id, document_id, status, data, embedding, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        rule.rule_id,
                        rule.document_id,
                        rule.status,
                        stamped.model_dump_json(),
                        json.dumps(vector) if vector is not None else None,
                        stamped.updated_at,
                    ),
                )

    def replace_rule(self, rule: Rule) -> None:
        """統合で出典を足したルールなど、内容を丸ごと置き換える。"""
        stamped = rule.model_copy(update={"updated_at": _now()})
        with self._connect() as conn:
            conn.execute(
                "UPDATE rules SET status = ?, data = ?, updated_at = ? WHERE rule_id = ?",
                (rule.status, stamped.model_dump_json(), stamped.updated_at, rule.rule_id),
            )

    def _rule(self, row: sqlite3.Row) -> Rule:
        rule = Rule.model_validate_json(row["data"])
        # ステータスは列を正とする
        return rule.model_copy(update={"status": row["status"], "updated_at": row["updated_at"]})

    def list_rules(self, status: RuleStatus | None = None) -> list[Rule]:
        query = "SELECT * FROM rules"
        params: tuple[str, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY document_id IS NOT NULL, rowid"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._rule(r) for r in rows]

    def get_rule(self, rule_id: str) -> Rule | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rules WHERE rule_id = ?", (rule_id,)).fetchone()
        return self._rule(row) if row else None

    def embeddings(self, status: RuleStatus | None = None) -> dict[str, list[float]]:
        """ルールID → 埋め込み（埋め込みの無いルールは含めない）。"""
        query = "SELECT rule_id, embedding FROM rules WHERE embedding IS NOT NULL"
        params: tuple[str, ...] = ()
        if status:
            query += " AND status = ?"
            params = (status,)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return {r["rule_id"]: json.loads(r["embedding"]) for r in rows}

    def update_rule(
        self, rule_id: str, update: RuleUpdate, *, embedding: list[float] | None = None
    ) -> Rule:
        rule = self.get_rule(rule_id)
        if rule is None:
            raise RuleNotFoundError(rule_id)
        changes = update.model_dump(exclude_none=True)
        if rule.builtin:
            # 組み込みのルールは、有効・無効の切り替えだけを許す
            changes = {k: v for k, v in changes.items() if k == "status"}
        if changes.get("check_type") == "mechanical" and rule.checker is None:
            changes.pop("check_type")  # 機械判定の種類が決まっていないルールは機械判定にできない
        updated = rule.model_copy(update={**changes, "updated_at": _now()})
        with self._connect() as conn:
            conn.execute(
                "UPDATE rules SET status = ?, data = ?, updated_at = ? WHERE rule_id = ?",
                (updated.status, updated.model_dump_json(), updated.updated_at, rule_id),
            )
            if embedding is not None or "rule" in changes:
                # 文面を変えたら、古い埋め込みは使わない（新しい埋め込みが無ければ消す）
                conn.execute(
                    "UPDATE rules SET embedding = ? WHERE rule_id = ?",
                    (json.dumps(embedding) if embedding is not None else None, rule_id),
                )
        return updated

    def ruleset_version(self) -> tuple[int, str | None]:
        """承認済みのルールの件数と、最後に更新された日時（ダッシュボードのメタ情報）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n, MAX(updated_at) AS t FROM rules WHERE status = 'approved'"
            ).fetchone()
        return int(row["n"]), row["t"]
