"""SQLite による保存。

- 指摘の本体は JSON で保存し、絞り込みに使う項目（カテゴリ・重大度・ステータス等）は列にも持つ
- 指摘のステータス（対応済み・却下と理由）は列を正とする
- 1つの解析ごとに、おいとまAPIの利用回数（エンドポイント・モデル・役割別）を保存する
- 日ごと（日本時間）の利用回数は解析とは別に持つ（論文を削除しても、その日の回数は減らさない）
- 論文と解析は所有者（owner_user_id）と公開範囲（visibility）を持つ。API から読むときは `*_for` で、
  主体（viewer。ゲストは None）が読めるものだけを SQL の条件で絞る（スキーマは core.store.schema）
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
from core.instructions.review import comparison_key
from core.llm.usage import UsageRow
from core.store.schema import connect, ensure_schema

DEFAULT_DB_PATH = "/data/db/mongai.sqlite3"

AnalysisStatus = Literal["queued", "running", "completed", "failed"]
Visibility = Literal["private", "public_demo", "quarantined"]

# 主体（viewer）が読める行の条件。ゲスト（None）は共通デモだけ
_READABLE = "(visibility = 'public_demo' OR (visibility = 'private' AND owner_user_id = ?))"


JST = timezone(timedelta(hours=9))


class FindingNotFoundError(KeyError):
    pass


class PaperBusyError(RuntimeError):
    """解析が順番待ち・実行中の論文は削除できない。"""


class PaperNotFoundError(KeyError):
    """主体が所有する論文が見つからない（他人の論文・共通デモ・隔離された論文を含む）。"""


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

    term: str  # 論文の中の用語
    query: str | None = None  # 実際に照会した語（日本語の論文では英語の対応語）
    request_id: str | None = None
    status: TermStatus
    matched: str | None = None  # 学外の辞書で該当した見出し
    description: str | None = None
    source: str | None = None
    reason: str | None = None
    updated_at: str = ""


class PaperRecord(BaseModel):
    doc_id: str
    filename: str
    size: int
    uploaded_at: str
    content_sha256: str | None = None
    owner_user_id: str | None = None
    visibility: Visibility = "private"


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
    owner_user_id: str | None = None
    visibility: Visibility = "private"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
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

    # ------------------------------------------------------------ アップロードされた論文

    def save_paper(
        self,
        doc_id: str,
        *,
        owner_user_id: str | None,
        filename: str,
        size: int,
        content_sha256: str | None = None,
        uploaded_at: str | None = None,
        visibility: Visibility = "private",
    ) -> None:
        """論文を登録する。同じ ID があれば sqlite3.IntegrityError（ID を作り直して再試行する）。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO papers (doc_id, filename, size, uploaded_at, content_sha256, "
                "owner_user_id, visibility) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    filename,
                    size,
                    uploaded_at or _now(),
                    content_sha256,
                    owner_user_id,
                    visibility,
                ),
            )

    def get_paper(self, doc_id: str) -> PaperRecord | None:
        """所有者を問わずに引く（ワーカー・内部の処理用。API からは get_paper_for を使う）。"""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM papers WHERE doc_id = ?", (doc_id,)).fetchone()
        return PaperRecord(**dict(row)) if row else None

    def get_paper_for(self, doc_id: str, viewer: str | None) -> PaperRecord | None:
        """主体が読める論文（自分の論文か共通デモ）。ゲストは viewer=None。"""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM papers WHERE doc_id = ? AND {_READABLE}", (doc_id, viewer)
            ).fetchone()
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
        owner_user_id: str,
        kind: str,
        settings: BaseModel,
        status: AnalysisStatus = "running",
    ) -> str:
        """所有者の解析を作る（所有者は論文の所有者と同じにする。呼び出し側で論文の所有権を確かめる）。"""
        analysis_id = f"A-{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO analyses (analysis_id, doc_id, kind, status, settings, created_at, "
                "owner_user_id, visibility) VALUES (?, ?, ?, ?, ?, ?, ?, 'private')",
                (
                    analysis_id,
                    doc_id,
                    kind,
                    status,
                    settings.model_dump_json(),
                    _now(),
                    owner_user_id,
                ),
            )
        return analysis_id

    def import_analysis(
        self, record: AnalysisRecord, findings: list[Finding], api_usage: list[UsageRow]
    ) -> None:
        """保存済みの解析結果（ID・日時を含む）を、共通デモ（所有者なし）として取り込む。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO analyses (analysis_id, doc_id, kind, status, settings, progress, "
                "warnings, error, created_at, finished_at, owner_user_id, visibility) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'public_demo')",
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
                    "SELECT analysis_id FROM analyses WHERE status = 'queued' "
                    "AND visibility = 'private' ORDER BY created_at"
                )
            ]
            conn.execute(
                "UPDATE analyses SET status = 'failed', finished_at = ?, "
                "error = 'ワーカーの再起動により解析が中断されました。もう一度解析してください' "
                "WHERE status = 'running'",
                (_now(),),
            )
        return running, queued

    def delete_paper(self, doc_id: str, *, owner_user_id: str) -> list[str]:
        """所有者の論文と、その論文のすべての解析結果を削除し、削除した解析の ID を返す。

        所有者の論文（private）でなければ PaperNotFoundError、解析が順番待ち・実行中なら
        PaperBusyError。PDF ファイルは、削除が確定してから呼び出し側で消す。
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")  # 確認から削除までの間に解析が始まらないようにする
            owned = conn.execute(
                "SELECT 1 FROM papers WHERE doc_id = ? AND owner_user_id = ? "
                "AND visibility = 'private'",
                (doc_id, owner_user_id),
            ).fetchone()
            if owned is None:
                raise PaperNotFoundError(doc_id)
            rows = conn.execute(
                "SELECT analysis_id, status, settings FROM analyses WHERE doc_id = ?", (doc_id,)
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
            for row in rows:
                snapshot = json.loads(row["settings"]).get("instruction_snapshot")
                draft_id = snapshot.get("draft_id") if snapshot else None
                if draft_id:
                    conn.execute(
                        "DELETE FROM instruction_drafts WHERE draft_id=? AND owner_user_id=? "
                        "AND NOT EXISTS (SELECT 1 FROM analyses WHERE "
                        "json_extract(settings, '$.instruction_snapshot.draft_id')=?)",
                        (draft_id, owner_user_id, draft_id),
                    )
            conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM papers WHERE doc_id = ?", (doc_id,))
        return ids

    def remove_stale_demos(self, *, keep_doc_id: str, keep_analysis_id: str) -> list[str]:
        """デモを差し替えた後に残る前のデモ（公開データ）を消し、消した論文の ID を返す。

        デモはアプリケーションが管理する合成データなので、新しいデモ以外の公開データは残さない。
        PDF ファイルは呼び出し側で消す。
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT analysis_id FROM analyses WHERE visibility = 'public_demo' "
                    "AND analysis_id != ?",
                    (keep_analysis_id,),
                )
            ]
            for table in ("findings", "paragraph_structures", "api_usage", "term_lookups"):
                conn.executemany(
                    f"DELETE FROM {table} WHERE analysis_id = ?",  # 表名は固定の4つだけ
                    [(i,) for i in ids],
                )
            conn.executemany("DELETE FROM analyses WHERE analysis_id = ?", [(i,) for i in ids])
            docs = [
                r[0]
                for r in conn.execute(
                    "SELECT doc_id FROM papers WHERE visibility = 'public_demo' AND doc_id != ?",
                    (keep_doc_id,),
                )
            ]
            conn.executemany("DELETE FROM documents WHERE doc_id = ?", [(d,) for d in docs])
            conn.executemany("DELETE FROM papers WHERE doc_id = ?", [(d,) for d in docs])
        return docs

    def list_analyses(self, viewer: str | None, limit: int = 100) -> list[AnalysisRecord]:
        """主体が読める解析（自分の解析と共通デモ）。件数の上限は絞り込みの後にかける。"""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM analyses WHERE {_READABLE} "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (viewer, limit),
            ).fetchall()
        return [self._analysis(r) for r in rows]

    def find_previous(self, analysis_id: str) -> AnalysisRecord | None:
        """同じ論文（同じ ID、または同じタイトル・ファイル名）の、これより前の完了した解析。

        候補は同じ所有者の解析だけ（他人の解析・共通デモは前回にしない）。共通デモには前回が無い。
        """
        current = self.get_analysis(analysis_id)
        if current is None or current.visibility != "private" or current.owner_user_id is None:
            return None
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT a.*, d.title AS title, p.filename AS filename FROM analyses a "
                "LEFT JOIN documents d ON d.doc_id = a.doc_id "
                "LEFT JOIN papers p ON p.doc_id = a.doc_id "
                "WHERE a.status = 'completed' AND (a.created_at < ? OR "
                "(a.created_at = ? AND a.rowid < "
                "(SELECT rowid FROM analyses WHERE analysis_id = ?))) "
                "AND a.owner_user_id = ? AND a.visibility = 'private' "
                "ORDER BY a.created_at DESC, a.rowid DESC",
                (current.created_at, current.created_at, analysis_id, current.owner_user_id),
            ).fetchall()
            mine = conn.execute(
                "SELECT d.title AS title, p.filename AS filename FROM analyses a "
                "LEFT JOIN documents d ON d.doc_id = a.doc_id "
                "LEFT JOIN papers p ON p.doc_id = a.doc_id WHERE a.analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        for row in rows:
            if comparison_key(json.loads(row["settings"])) != comparison_key(current.settings):
                continue
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
            owner_user_id=row["owner_user_id"],
            visibility=row["visibility"],
        )

    def finish_analysis(
        self,
        analysis_id: str,
        *,
        status: AnalysisStatus,
        warnings: list[str] | None = None,
        error: str | None = None,
        api_usage: list[UsageRow] | None = None,
        only_if_queued: bool = False,
    ) -> None:
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE analyses SET status = ?, warnings = ?, error = ?, finished_at = ? "
                "WHERE analysis_id = ?" + (" AND status = 'queued'" if only_if_queued else ""),
                (
                    status,
                    json.dumps(warnings or [], ensure_ascii=False),
                    error,
                    _now(),
                    analysis_id,
                ),
            ).rowcount
            if changed and api_usage:
                self._save_usage(conn, analysis_id, api_usage)

    def get_analysis(self, analysis_id: str) -> AnalysisRecord | None:
        """所有者を問わずに引く（ワーカー・内部の処理用。API からは get_analysis_for を使う）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
        return self._analysis(row) if row is not None else None

    def get_analysis_for(self, analysis_id: str, viewer: str | None) -> AnalysisRecord | None:
        """主体が読める解析（自分の解析か共通デモ）。ゲストは viewer=None。"""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM analyses WHERE analysis_id = ? AND {_READABLE}",
                (analysis_id, viewer),
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
                "INSERT OR REPLACE INTO term_lookups (analysis_id, term, query, request_id, status, "
                "matched, description, source, reason, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        analysis_id,
                        r.term,
                        r.query,
                        r.request_id,
                        r.status,
                        r.matched,
                        r.description,
                        r.source,
                        r.reason,
                        now,
                    )
                    for r in records
                ],
            )

    def get_term_lookups(self, analysis_id: str) -> list[TermLookupRecord]:
        """解析の照会の記録に、承認キューでの決定（あれば）を重ねて返す。

        重ねるのは、解析の所有者が決定した結果だけ（共通デモには重ねず、保存済みの状態を表示する）。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.term, t.query, t.request_id, "
                "COALESCE(d.status, t.status) AS status, "
                "CASE WHEN d.request_id IS NULL THEN t.matched ELSE d.matched END AS matched, "
                "CASE WHEN d.request_id IS NULL THEN t.description ELSE d.description END "
                "AS description, "
                "CASE WHEN d.request_id IS NULL THEN t.source ELSE d.source END AS source, "
                "t.reason, COALESCE(d.decided_at, t.updated_at) AS updated_at "
                "FROM term_lookups t JOIN analyses a ON a.analysis_id = t.analysis_id "
                "LEFT JOIN term_decisions d ON d.request_id = t.request_id "
                "AND d.owner_user_id = a.owner_user_id "
                "WHERE t.analysis_id = ? ORDER BY t.rowid",
                (analysis_id,),
            ).fetchall()
        return [TermLookupRecord(**dict(r)) for r in rows]

    def decide_term_lookup(
        self,
        request_id: str,
        status: TermStatus,
        *,
        owner_user_id: str,
        matched: str | None = None,
        description: str | None = None,
        source: str | None = None,
    ) -> None:
        """承認キューで承認・却下した結果を、要求ごとに記録する。

        その要求を積んだすべての解析に反映される（解析がまだ終わっておらず、照会の記録が保存される前に
        承認した場合も、後から保存された記録に重ねて表示する）。
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO term_decisions (request_id, owner_user_id, status, matched, "
                "description, source, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (request_id, owner_user_id, status, matched, description, source, _now()),
            )

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

    def add_daily_usage(self, day: str, rows: list[UsageRow], *, user_id: str | None) -> None:
        """その日の利用回数に rows（前回からの増分）を足す（全体と、user_id があればその利用者の分）。"""
        rows = [r for r in rows if r.requests or r.prompt_tokens or r.completion_tokens]
        with self._connect() as conn:
            if user_id is not None:
                conn.executemany(
                    "INSERT INTO api_usage_daily_by_user (day, user_id, endpoint, model, requests, "
                    "prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(day, user_id, endpoint, model) DO UPDATE SET "
                    "requests = requests + excluded.requests, "
                    "prompt_tokens = prompt_tokens + excluded.prompt_tokens, "
                    "completion_tokens = completion_tokens + excluded.completion_tokens",
                    [
                        (
                            day,
                            user_id,
                            r.endpoint,
                            r.model,
                            r.requests,
                            r.prompt_tokens,
                            r.completion_tokens,
                        )
                        for r in rows
                    ],
                )
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
                ],
            )

    def get_daily_usage(self, day: str, *, user_id: str) -> DailyUsage:
        """その利用者の、その日の利用回数（全体の合計は一般の利用者には見せない）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(requests), 0) AS requests, "
                "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens), 0) AS completion_tokens "
                "FROM api_usage_daily_by_user WHERE day = ? AND user_id = ?",
                (day, user_id),
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
