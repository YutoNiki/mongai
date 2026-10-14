"""ルールとルール資料の保存（SQLite。論文のメタデータと同じデータベースの別のテーブル）。

ルールの埋め込み（重複の統合・ルールの検索に使う）も同じ行に JSON で持つ。ルールは多くても数百件なので、
ベクタDB は使わず全件と比べる（TODO.md の決定事項）。

ルール資料とルールは利用者個人のもので、読み書きには所有者（owner_user_id）を必ず指定する。
組み込みのルールは全員で共有し（visibility が builtin）、有効・無効と統合された出典は
利用者ごとの設定（rule_user_settings）に持つ。
"""

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from core.rules.models import (
    BUILTIN_RULES,
    Rule,
    RuleDocument,
    RuleSource,
    RuleStatus,
    RuleUpdate,
)
from core.store.db import DEFAULT_DB_PATH
from core.store.schema import connect, ensure_schema

BUILTIN_IDS = frozenset(r.rule_id for r in BUILTIN_RULES)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RuleNotFoundError(KeyError):
    pass


class RuleStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("MONGAI_DB_PATH") or DEFAULT_DB_PATH)
        ensure_schema(self.path)
        with self._connect() as conn:
            # 組み込みのルールは無ければ入れる（共有の本体。利用者ごとの設定は rule_user_settings）
            for rule in BUILTIN_RULES:
                conn.execute(
                    "INSERT OR IGNORE INTO rules (rule_id, document_id, status, data, updated_at, "
                    "owner_user_id, visibility) VALUES (?, NULL, ?, ?, ?, NULL, 'builtin')",
                    (rule.rule_id, rule.status, rule.model_dump_json(), _now()),
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------ ルール資料

    def create_document(self, owner_user_id: str, filename: str, kind: str) -> RuleDocument:
        doc = RuleDocument(
            document_id=f"RD-{uuid.uuid4().hex[:12]}",
            filename=filename,
            kind=kind,
            status="queued",
            created_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO rule_documents (document_id, filename, kind, status, created_at, "
                "owner_user_id, visibility) VALUES (?, ?, ?, ?, ?, ?, 'private')",
                (
                    doc.document_id,
                    doc.filename,
                    doc.kind,
                    doc.status,
                    doc.created_at,
                    owner_user_id,
                ),
            )
        return doc

    @staticmethod
    def _document(row: sqlite3.Row) -> RuleDocument:
        data = dict(row)
        data.pop("owner_user_id", None)
        data.pop("visibility", None)
        return RuleDocument(**data)

    def get_document(self, owner_user_id: str, document_id: str) -> RuleDocument | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM rule_documents WHERE document_id = ? AND owner_user_id = ? "
                "AND visibility = 'private'",
                (document_id, owner_user_id),
            ).fetchone()
        return self._document(row) if row else None

    def document_owner(self, document_id: str) -> str | None:
        """ワーカー用：資料の所有者（所有者のいない資料は None）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_user_id FROM rule_documents WHERE document_id = ? "
                "AND visibility = 'private'",
                (document_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def list_documents(self, owner_user_id: str) -> list[RuleDocument]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM rule_documents WHERE owner_user_id = ? AND visibility = 'private' "
                "ORDER BY created_at DESC, rowid DESC",
                (owner_user_id,),
            ).fetchall()
        return [self._document(r) for r in rows]

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

    def delete_document(self, owner_user_id: str, document_id: str) -> bool:
        """所有者の資料と、そこから抽出したルールを消す。資料が無ければ False。"""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM rules WHERE document_id = ? AND owner_user_id = ?",
                (document_id, owner_user_id),
            )
            cursor = conn.execute(
                "DELETE FROM rule_documents WHERE document_id = ? AND owner_user_id = ?",
                (document_id, owner_user_id),
            )
        return cursor.rowcount > 0

    def recover_interrupted(self) -> list[str]:
        """ワーカーの再起動で中断された抽出を失敗にし、順番待ちの資料の ID を返す（全員の分。積み直す）。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE rule_documents SET status = 'failed', error = ?, finished_at = ? "
                "WHERE status = 'running'",
                ("ワーカーの再起動で中断されました。資料を登録し直してください", _now()),
            )
            rows = conn.execute(
                "SELECT document_id FROM rule_documents WHERE status = 'queued' "
                "AND visibility = 'private'"
            ).fetchall()
        return [r["document_id"] for r in rows]

    # ------------------------------------------------------------ ルール

    def add_rules(
        self, owner_user_id: str, rules: list[Rule], embeddings: list[list[float] | None]
    ) -> None:
        with self._connect() as conn:
            for rule, vector in zip(rules, embeddings, strict=True):
                stamped = rule.model_copy(update={"updated_at": _now()})
                conn.execute(
                    "INSERT INTO rules (rule_id, document_id, status, data, embedding, updated_at, "
                    "owner_user_id, visibility) VALUES (?, ?, ?, ?, ?, ?, ?, 'private')",
                    (
                        rule.rule_id,
                        rule.document_id,
                        rule.status,
                        stamped.model_dump_json(),
                        json.dumps(vector) if vector is not None else None,
                        stamped.updated_at,
                        owner_user_id,
                    ),
                )

    def replace_rule(self, owner_user_id: str, rule: Rule) -> None:
        """統合で出典を足したルールなど、内容を丸ごと置き換える。

        組み込みのルールは共有の本体を変えず、この利用者の設定（統合された出典）だけを変える。
        """
        if rule.rule_id in BUILTIN_IDS:
            self._save_setting(owner_user_id, rule.rule_id, merged_sources=rule.merged_sources)
            return
        stamped = rule.model_copy(update={"updated_at": _now()})
        with self._connect() as conn:
            conn.execute(
                "UPDATE rules SET status = ?, data = ?, updated_at = ? WHERE rule_id = ? "
                "AND owner_user_id = ? AND visibility = 'private'",
                (
                    rule.status,
                    stamped.model_dump_json(),
                    stamped.updated_at,
                    rule.rule_id,
                    owner_user_id,
                ),
            )

    def _save_setting(
        self,
        owner_user_id: str,
        rule_id: str,
        *,
        status: RuleStatus | None = None,
        merged_sources: list[RuleSource] | None = None,
    ) -> None:
        sources = (
            json.dumps([s.model_dump(mode="json") for s in merged_sources], ensure_ascii=False)
            if merged_sources is not None
            else None
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO rule_user_settings (user_id, rule_id, status, extra_sources, "
                "updated_at) VALUES (?, ?, ?, COALESCE(?, '[]'), ?) "
                "ON CONFLICT(user_id, rule_id) DO UPDATE SET "
                "status = COALESCE(excluded.status, status), "
                "extra_sources = COALESCE(?, extra_sources), updated_at = excluded.updated_at",
                (owner_user_id, rule_id, status, sources, _now(), sources),
            )

    def _rules(self, conn: sqlite3.Connection, owner_user_id: str) -> list[tuple[Rule, str | None]]:
        """利用者のルールと組み込みのルール（本人の設定を重ねたもの）と、その埋め込み。"""
        rows = conn.execute(
            "SELECT r.*, s.status AS my_status, s.extra_sources AS my_sources FROM rules r "
            "LEFT JOIN rule_user_settings s ON s.rule_id = r.rule_id AND s.user_id = ? "
            "WHERE (r.visibility = 'private' AND r.owner_user_id = ?) OR r.visibility = 'builtin' "
            "ORDER BY r.document_id IS NOT NULL, r.rowid",
            (owner_user_id, owner_user_id),
        ).fetchall()
        result = []
        for row in rows:
            rule = Rule.model_validate_json(row["data"])
            # ステータスは列を正とする（組み込みのルールは本人の設定があればそれを使う）
            status = row["status"]
            update: dict[str, object] = {"updated_at": row["updated_at"]}
            if row["visibility"] == "builtin":
                status = row["my_status"] or status
                if row["my_sources"]:
                    update["merged_sources"] = [
                        RuleSource.model_validate(s) for s in json.loads(row["my_sources"])
                    ]
            result.append((rule.model_copy(update={**update, "status": status}), row["embedding"]))
        return result

    def list_rules(self, owner_user_id: str, status: RuleStatus | None = None) -> list[Rule]:
        with self._connect() as conn:
            rules = self._rules(conn, owner_user_id)
        return [r for r, _ in rules if status is None or r.status == status]

    def get_rule(self, owner_user_id: str, rule_id: str) -> Rule | None:
        return next((r for r in self.list_rules(owner_user_id) if r.rule_id == rule_id), None)

    def embeddings(
        self, owner_user_id: str, status: RuleStatus | None = None
    ) -> dict[str, list[float]]:
        """ルールID → 埋め込み（利用者のルールと組み込みのルール。埋め込みの無いルールは含めない）。"""
        with self._connect() as conn:
            rules = self._rules(conn, owner_user_id)
        return {
            r.rule_id: json.loads(e)
            for r, e in rules
            if e is not None and (status is None or r.status == status)
        }

    def update_rule(
        self,
        owner_user_id: str,
        rule_id: str,
        update: RuleUpdate,
        *,
        embedding: list[float] | None = None,
    ) -> Rule:
        rule = self.get_rule(owner_user_id, rule_id)
        if rule is None:
            raise RuleNotFoundError(rule_id)
        changes = update.model_dump(exclude_none=True)
        if rule.builtin:
            # 組み込みのルールは、有効・無効の切り替えだけを許す（本人の設定として保存する）
            if "status" in changes:
                self._save_setting(owner_user_id, rule_id, status=changes["status"])
            return rule.model_copy(update={k: v for k, v in changes.items() if k == "status"})
        if changes.get("check_type") == "mechanical" and rule.checker is None:
            changes.pop("check_type")  # 機械判定の種類が決まっていないルールは機械判定にできない
        updated = rule.model_copy(update={**changes, "updated_at": _now()})
        with self._connect() as conn:
            conn.execute(
                "UPDATE rules SET status = ?, data = ?, updated_at = ? WHERE rule_id = ? "
                "AND owner_user_id = ?",
                (
                    updated.status,
                    updated.model_dump_json(),
                    updated.updated_at,
                    rule_id,
                    owner_user_id,
                ),
            )
            if embedding is not None or "rule" in changes:
                # 文面を変えたら、古い埋め込みは使わない（新しい埋め込みが無ければ消す）
                conn.execute(
                    "UPDATE rules SET embedding = ? WHERE rule_id = ? AND owner_user_id = ?",
                    (
                        json.dumps(embedding) if embedding is not None else None,
                        rule_id,
                        owner_user_id,
                    ),
                )
        return updated

    def ruleset_version(self, owner_user_id: str) -> tuple[int, str | None]:
        """利用者が使う承認済みのルールの件数と、最後に更新された日時（ダッシュボードのメタ情報）。"""
        approved = self.list_rules(owner_user_id, status="approved")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(updated_at) FROM rule_user_settings WHERE user_id = ?",
                (owner_user_id,),
            ).fetchone()
        times = [r.updated_at for r in approved if r.updated_at] + ([row[0]] if row[0] else [])
        return len(approved), max(times) if times else None
