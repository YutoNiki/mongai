"""要件の保存。APIには所有者を必須とし、ワーカーだけがIDから所有者を取得する。"""

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from core.instructions.models import (
    DocumentType,
    Draft,
    Profile,
    ProfileRef,
    ProfileScope,
    Requirement,
    RequirementResult,
    Snapshot,
    detect_conflicts,
)
from core.rules.models import Rule
from core.store.schema import connect


class InstructionNotFoundError(KeyError):
    pass


class InstructionConflictError(ValueError):
    pass


class InstructionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def list_profiles(self, owner: str) -> list[Profile]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM instruction_profiles WHERE owner_user_id = ? ORDER BY rowid DESC",
                (owner,),
            ).fetchall()
        return [Profile.model_validate_json(r[0]) for r in rows]

    def get_profile(self, owner: str, profile_id: str) -> Profile:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM instruction_profiles WHERE profile_id = ? AND owner_user_id = ?",
                (profile_id, owner),
            ).fetchone()
        if row is None:
            raise InstructionNotFoundError(profile_id)
        return Profile.model_validate_json(row[0])

    def save_profile(
        self,
        owner: str,
        name: str,
        scope: ProfileScope,
        draft_id: str,
        draft_revision: int,
        *,
        profile_id: str | None = None,
        revision: int | None = None,
    ) -> Profile:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            draft = self._draft(conn, owner, draft_id)
            if draft.revision != draft_revision or not draft.confirmed:
                raise InstructionConflictError("確認済みの最新の要件を指定してください")
            if not any(r.enabled for r in draft.requirements):
                raise InstructionConflictError("保存する確認項目を1件以上指定してください")
            if profile_id:
                row = conn.execute(
                    "SELECT revision FROM instruction_profiles WHERE profile_id = ? AND owner_user_id = ?",
                    (profile_id, owner),
                ).fetchone()
                if row is None:
                    raise InstructionNotFoundError(profile_id)
                if row[0] != revision:
                    raise InstructionConflictError("設定が更新されています。読み込み直してください")
            profile = Profile(
                profile_id=profile_id or f"IP-{uuid.uuid4().hex[:12]}",
                name=name,
                scope=scope,
                raw_text=draft.raw_text,
                requirements=[r for r in draft.requirements if r.enabled],
                revision=(revision or 0) + 1,
                updated_at=datetime.now(UTC).isoformat(),
            )
            conn.execute(
                "INSERT INTO instruction_profiles VALUES (?, ?, ?, ?) "
                "ON CONFLICT(profile_id) DO UPDATE SET revision=excluded.revision, data=excluded.data",
                (profile.profile_id, owner, profile.revision, profile.model_dump_json()),
            )
        return profile

    def delete_profile(self, owner: str, profile_id: str) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM instruction_profiles WHERE profile_id = ? AND owner_user_id = ?",
                (profile_id, owner),
            )
            if not cursor.rowcount:
                raise InstructionNotFoundError(profile_id)

    def create_draft(
        self,
        owner: str,
        document_type: DocumentType,
        raw_text: str,
        refs: list[ProfileRef],
        *,
        legacy_rules: list[Rule] | None = None,
    ) -> Draft:
        profiles = [self.get_profile(owner, ref.profile_id) for ref in refs]
        if len({p.profile_id for p in profiles}) != len(profiles):
            raise InstructionConflictError("同じ設定を重ねて指定できません")
        if any(p.revision != ref.revision for p, ref in zip(profiles, refs, strict=True)):
            raise InstructionConflictError("設定が更新されています。読み込み直してください")
        requirements = [
            r.model_copy(
                update={
                    "requirement_id": f"REQ-{uuid.uuid4().hex[:12]}",
                    "source": f"{p.name}（版{p.revision}）",
                }
            )
            for p in profiles
            for r in p.requirements
            if r.enabled
        ]
        if len(requirements) > 50:
            raise InstructionConflictError("確認項目は合計50件以内にしてください")
        draft = Draft(
            draft_id=f"ID-{uuid.uuid4().hex[:12]}",
            document_type=document_type,
            raw_text=raw_text,
            profiles=profiles,
            requirements=requirements,
            status="queued" if raw_text.strip() else "ready",
            conflicts=detect_conflicts(requirements),
            legacy_rules=legacy_rules or [],
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO instruction_drafts VALUES (?, ?, ?, ?, ?)",
                (
                    draft.draft_id,
                    owner,
                    draft.status,
                    draft.revision,
                    draft.model_dump_json(),
                ),
            )
        return draft

    @staticmethod
    def _draft(conn: sqlite3.Connection, owner: str, draft_id: str) -> Draft:
        row = conn.execute(
            "SELECT data FROM instruction_drafts WHERE draft_id = ? AND owner_user_id = ?",
            (draft_id, owner),
        ).fetchone()
        if row is None:
            raise InstructionNotFoundError(draft_id)
        return Draft.model_validate_json(row[0])

    def get_draft(self, owner: str, draft_id: str) -> Draft:
        with self._connect() as conn:
            return self._draft(conn, owner, draft_id)

    def update_draft(
        self,
        owner: str,
        draft_id: str,
        revision: int,
        requirements: list[Requirement] | None,
        *,
        confirm: bool = False,
        excluded_rule_ids: list[str] | None = None,
    ) -> Draft:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            draft = self._draft(conn, owner, draft_id)
            if draft.status != "ready" or draft.revision != revision:
                raise InstructionConflictError(
                    "確認項目が更新されたか、抽出中です。読み込み直してください"
                )
            items = draft.requirements if requirements is None else requirements
            excluded = draft.excluded_rule_ids if excluded_rule_ids is None else excluded_rule_ids
            if set(excluded) - {r.rule_id for r in draft.legacy_rules}:
                raise InstructionConflictError("この確認で使える既存ルールを指定してください")
            if len({r.requirement_id for r in items}) != len(items):
                raise InstructionConflictError("確認項目のIDが重複しています")
            conflicts = detect_conflicts(items)
            if confirm and conflicts:
                raise InstructionConflictError("両立しない条件を編集・除外してから確定してください")
            draft = draft.model_copy(
                update={
                    "requirements": items,
                    "conflicts": conflicts,
                    "revision": draft.revision + 1,
                    "confirmed": confirm,
                    "excluded_rule_ids": excluded,
                }
            )
            self._write_draft(conn, draft)
        return draft

    @staticmethod
    def _write_draft(conn: sqlite3.Connection, draft: Draft) -> None:
        conn.execute(
            "UPDATE instruction_drafts SET data=?, status=?, revision=? WHERE draft_id=?",
            (draft.model_dump_json(), draft.status, draft.revision, draft.draft_id),
        )

    def fail_queued_extraction(self, owner: str, draft_id: str, error: str) -> None:
        """キューへの登録に失敗した確認項目を、順番待ちのまま残さない。"""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            draft = self._draft(conn, owner, draft_id)
            if draft.status == "queued":
                self._write_draft(
                    conn,
                    draft.model_copy(
                        update={
                            "status": "failed",
                            "error": error,
                            "revision": draft.revision + 1,
                        }
                    ),
                )

    def claim_extraction(self, draft_id: str) -> tuple[str, Draft] | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner_user_id FROM instruction_drafts WHERE draft_id=? AND status='queued'",
                (draft_id,),
            ).fetchone()
            if row is None:
                return None
            owner = str(row[0])
            draft = self._draft(conn, owner, draft_id).model_copy(update={"status": "running"})
            self._write_draft(conn, draft)
            return owner, draft

    def finish_extraction(
        self,
        owner: str,
        draft_id: str,
        requirements: list[Requirement],
        *,
        error: str | None = None,
    ) -> Draft:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            draft = self._draft(conn, owner, draft_id)
            if draft.status != "running":
                return draft
            items = [*draft.requirements, *requirements]
            if len(items) > 50:
                error = "確認項目が50件を超えました。条件を整理して作り直してください"
            draft = draft.model_copy(
                update={
                    "requirements": items if error is None else draft.requirements,
                    "status": "failed" if error else "ready",
                    "error": error,
                    "conflicts": detect_conflicts(items) if error is None else [],
                    "revision": draft.revision + 1,
                }
            )
            self._write_draft(conn, draft)
            return draft

    def recover_interrupted(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM instruction_drafts WHERE status='running'"
            ).fetchall()
            for row in rows:
                draft = Draft.model_validate_json(row[0]).model_copy(
                    update={
                        "status": "failed",
                        "error": "処理が中断されました。確認項目を作り直してください",
                        "revision": Draft.model_validate_json(row[0]).revision + 1,
                    }
                )
                self._write_draft(conn, draft)
            return [
                r[0]
                for r in conn.execute(
                    "SELECT draft_id FROM instruction_drafts WHERE status='queued'"
                )
            ]

    def create_analysis(
        self,
        owner: str,
        doc_id: str,
        settings: BaseModel,
        draft_id: str,
        revision: int,
        document_type: DocumentType,
    ) -> str:
        """ドラフトをロックして固定。設定編集と解析作成の間に割り込みを入れない。"""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            draft = self._draft(conn, owner, draft_id)
            if (
                not draft.confirmed
                or draft.revision != revision
                or draft.document_type != document_type
            ):
                raise InstructionConflictError("文書種別と確認済みの最新の要件を指定してください")
            owned = conn.execute(
                "SELECT 1 FROM papers WHERE doc_id=? AND owner_user_id=? AND visibility='private'",
                (doc_id, owner),
            ).fetchone()
            if owned is None:
                raise InstructionNotFoundError(doc_id)
            snapshot = Snapshot(
                draft_id=draft.draft_id,
                document_type=document_type,
                profiles=draft.profiles,
                raw_text=draft.raw_text,
                requirements=draft.requirements,
                excluded_rule_ids=draft.excluded_rule_ids,
                legacy_rules=draft.legacy_rules,
            )
            data = settings.model_dump(mode="json")
            data["instruction_snapshot"] = snapshot.model_dump(mode="json")
            # Store と同じ形式。固定条件と解析はこのトランザクション内で保存する。
            analysis_id = f"A-{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO analyses (analysis_id, doc_id, kind, status, settings, created_at, "
                "owner_user_id, visibility) VALUES (?, ?, 'logic', 'queued', ?, ?, ?, 'private')",
                (
                    analysis_id,
                    doc_id,
                    json.dumps(data, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                    owner,
                ),
            )
        return analysis_id

    def save_results(self, analysis_id: str, results: list[RequirementResult]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO analysis_requirement_results VALUES (?, ?, ?) "
                "ON CONFLICT(analysis_id, requirement_id) DO UPDATE SET data=excluded.data",
                [(analysis_id, r.requirement_id, r.model_dump_json()) for r in results],
            )

    def results(self, analysis_id: str) -> list[RequirementResult]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM analysis_requirement_results WHERE analysis_id=? ORDER BY rowid",
                (analysis_id,),
            ).fetchall()
        return [RequirementResult.model_validate_json(row[0]) for row in rows]
