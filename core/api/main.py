"""core の FastAPI アプリ。`web`（nginx）から `/api` 以下が中継される。

認証は無い（web は 127.0.0.1 にだけ公開し、SSH のポートフォワード等で利用する前提）。
"""

import contextlib
import csv
import io
import json
import os
from typing import Annotated, Any, Literal, Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Depends, FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import Response

from core.analyze.models import Finding
from core.analyze.pipeline import AnalysisSettings
from core.annotate.labels import category_label, severity_label, subcategory_label
from core.annotate.pdf import annotate_pdf
from core.api.schemas import (
    AnalysisDiff,
    AnalysisListItem,
    AnalysisSummary,
    AuditLog,
    BulkRuleStatus,
    CreateAnalysisRequest,
    DocumentInfo,
    PersistingPair,
    PreviousAnalysis,
    StatusUpdate,
    UploadResponse,
)
from core.api.summary import count_findings
from core.gateway_client import GatewayClient, GatewayError
from core.ingest.textdoc import SUFFIXES, looks_valid
from core.lab.extract import delete_lab_file
from core.lab.extract import save_document_file as save_lab_file
from core.lab.models import LabComment, LabDocument, current_lab_id
from core.lab.store import LabStore
from core.llm.config import get_models_config
from core.logging_config import configure_logging
from core.report.diff import FindingsDiff, diff_findings
from core.report.text import text_report
from core.rules.extract import delete_rule_file, save_document_file
from core.rules.models import Rule, RuleDocument, RuleStatus, RuleUpdate
from core.rules.store import RuleNotFoundError, RuleStore
from core.sample import is_sample
from core.store import (
    AnalysisRecord,
    DailyUsage,
    FindingNotFoundError,
    PaperBusyError,
    Store,
    today_jst,
)
from core.store.files import delete_paper_file, load_paper, save_paper

configure_logging()

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
# ダッシュボードのメタ情報に表示する役割
DISPLAYED_ROLES = (
    "structure_extraction",
    "logic_check",
    "second_opinion",
    "citation_support",
    "suggestion",
)

app = FastAPI(title="MongAI core", docs_url="/api/docs", openapi_url="/api/openapi.json")


class JobQueue(Protocol):
    async def enqueue(self, analysis_id: str) -> None: ...

    async def enqueue_rules(self, document_id: str) -> None: ...

    async def enqueue_lab(self, document_id: str) -> None: ...


class ArqQueue:
    """解析ジョブを Redis（arq）に積む。"""

    def __init__(self) -> None:
        self._pool: ArqRedis | None = None

    async def _redis(self) -> ArqRedis:
        if self._pool is None:
            dsn = os.environ.get("REDIS_URL", "redis://redis:6379/0")
            self._pool = await create_pool(RedisSettings.from_dsn(dsn))
        return self._pool

    async def enqueue(self, analysis_id: str) -> None:
        pool = await self._redis()
        await pool.enqueue_job("run_analysis", analysis_id, _job_id=analysis_id)

    async def enqueue_rules(self, document_id: str) -> None:
        pool = await self._redis()
        await pool.enqueue_job("run_rule_extraction", document_id, _job_id=document_id)

    async def enqueue_lab(self, document_id: str) -> None:
        pool = await self._redis()
        await pool.enqueue_job("run_lab_extraction", document_id, _job_id=document_id)


_queue = ArqQueue()


def get_store() -> Store:
    return Store()


def get_rule_store() -> RuleStore:
    return RuleStore()


def get_lab_store() -> LabStore:
    return LabStore()


def get_queue() -> JobQueue:
    return _queue


def get_gateway() -> GatewayClient:
    return GatewayClient()


StoreDep = Annotated[Store, Depends(get_store)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
GatewayDep = Annotated[GatewayClient, Depends(get_gateway)]
RuleStoreDep = Annotated[RuleStore, Depends(get_rule_store)]
LabStoreDep = Annotated[LabStore, Depends(get_lab_store)]


def _analysis_or_404(store: Store, analysis_id: str) -> AnalysisRecord:
    record = store.get_analysis(analysis_id)
    if record is None:
        raise HTTPException(status_code=404, detail="解析が見つかりません")
    return record


@app.get("/api/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------- 論文


@app.post("/api/documents", status_code=201)
async def upload_document(file: UploadFile, store: StoreDep) -> UploadResponse:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="ファイルが大きすぎます（上限 100MB）")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="PDF ファイルを選んでください")
    doc_id = save_paper(data)
    filename = os.path.basename(file.filename or "paper.pdf")[:255]
    store.save_paper(doc_id, filename=filename, size=len(data))
    return UploadResponse(doc_id=doc_id, filename=filename, size=len(data))


@app.get("/api/documents/{doc_id}/paper.pdf")
def paper_pdf(doc_id: str) -> Response:
    try:
        data = load_paper(doc_id)
    except ValueError:
        data = None
    if data is None:
        raise HTTPException(status_code=404, detail="論文のファイルが見つかりません")
    return Response(content=data, media_type="application/pdf")


@app.delete("/api/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str, store: StoreDep) -> Response:
    """論文の PDF と、その論文のすべての解析結果を削除する（外部通信ログは追記専用なので残る）。"""
    if store.get_paper(doc_id) is None:
        raise HTTPException(status_code=404, detail="論文が見つかりません")
    if is_sample(doc_id):
        raise HTTPException(status_code=403, detail="例の論文は削除できません")
    try:
        store.delete_paper(doc_id)
    except PaperBusyError:
        raise HTTPException(
            status_code=409,
            detail="解析中・順番待ちの論文は削除できません。解析が終わってから削除してください",
        ) from None
    delete_paper_file(doc_id)
    return Response(status_code=204)


# ---------------------------------------------------------------- APIの利用回数


@app.get("/api/usage/today")
def usage_today(store: StoreDep) -> DailyUsage:
    """今日（日本時間）の おいとまAPI の利用回数とトークン数。解析中の分は約10秒ごとに加わる。"""
    return store.get_daily_usage(today_jst())


# ---------------------------------------------------------------- 解析


@app.post("/api/analyses", status_code=201)
async def create_analysis(
    request: CreateAnalysisRequest, store: StoreDep, queue: QueueDep, gateway: GatewayDep
) -> AnalysisSummary:
    if store.get_paper(request.doc_id) is None:
        raise HTTPException(status_code=404, detail="論文が見つかりません")
    settings = AnalysisSettings(output_lang=request.output_lang)
    analysis_id = store.create_analysis(
        request.doc_id, kind="logic", settings=settings, status="queued"
    )
    await queue.enqueue(analysis_id)
    return await summarize(store, analysis_id, gateway)


@app.get("/api/analyses")
def list_analyses(store: StoreDep) -> list[AnalysisListItem]:
    items = []
    for record in store.list_analyses():
        doc = store.get_document(record.doc_id)
        paper = store.get_paper(record.doc_id)
        findings = store.get_findings(record.analysis_id)
        items.append(
            AnalysisListItem(
                analysis_id=record.analysis_id,
                doc_id=record.doc_id,
                title=doc.title if doc else None,
                filename=paper.filename if paper else None,
                status=record.status,
                created_at=record.created_at,
                finished_at=record.finished_at,
                total=sum(f.status != "dismissed" for f in findings),
                progress=record.progress,
                sample=is_sample(record.doc_id),
            )
        )
    return items


async def summarize(store: Store, analysis_id: str, gateway: GatewayClient) -> AnalysisSummary:
    record = store.get_analysis(analysis_id)
    if record is None:
        raise HTTPException(status_code=404, detail="解析が見つかりません")
    doc = store.get_document(record.doc_id)
    counts = count_findings(store.get_findings(analysis_id), doc)
    previous, _ = _diff(store, analysis_id)
    config = get_models_config()
    usage = store.get_api_usage(analysis_id)
    try:
        external = await gateway.external_count(analysis_id)
    except GatewayError:
        external = 0
    return AnalysisSummary(
        analysis=record,
        paper=store.get_paper(record.doc_id),
        document=DocumentInfo(title=doc.title, lang=doc.lang, page_count=doc.page_count)
        if doc
        else None,
        counts=counts,
        previous=previous,
        models={role: config.role(role).model for role in DISPLAYED_ROLES},
        api_usage=usage,
        api_requests=sum(r.requests for r in usage),
        external_requests=external,
        sample=is_sample(record.doc_id),
    )


@app.get("/api/analyses/{analysis_id}")
async def get_analysis(analysis_id: str, store: StoreDep, gateway: GatewayDep) -> AnalysisSummary:
    return await summarize(store, analysis_id, gateway)


Severity = Literal["critical", "major", "suggestion"]
Category = Literal["logic", "readability", "rule", "citation", "lab"]
Status = Literal["open", "accepted", "dismissed"]
Confidence = Literal["high", "low"]


def _filtered(
    store: Store,
    analysis_id: str,
    category: Category | None,
    severity: Severity | None,
    section_id: str | None,
    confidence: Confidence | None,
    status: Status | None,
) -> list[Finding]:
    _analysis_or_404(store, analysis_id)
    findings = store.get_findings(
        analysis_id,
        category=category,
        severity=severity,
        section_id=section_id,
        confidence=confidence,
        status=status,
    )
    # 確度で絞るときは、確度を示さない（多数決をしていない）指摘を除く
    return [f for f in findings if f.voted] if confidence else findings


@app.get("/api/analyses/{analysis_id}/findings")
def list_findings(
    analysis_id: str,
    store: StoreDep,
    category: Category | None = None,
    severity: Severity | None = None,
    section_id: str | None = None,
    confidence: Confidence | None = None,
    status: Status | None = None,
) -> list[Finding]:
    return _filtered(store, analysis_id, category, severity, section_id, confidence, status)


@app.patch("/api/analyses/{analysis_id}/findings/{finding_id}")
def update_finding(
    analysis_id: str, finding_id: str, update: StatusUpdate, store: StoreDep
) -> Finding:
    reason = (update.reason or "").strip() or None
    if update.status == "dismissed" and reason is None:
        raise HTTPException(status_code=422, detail="却下する理由を入力してください")
    try:
        store.set_finding_status(analysis_id, finding_id, update.status, reason)
    except FindingNotFoundError:
        raise HTTPException(status_code=404, detail="指摘が見つかりません") from None
    return next(f for f in store.get_findings(analysis_id) if f.finding_id == finding_id)


@app.get("/api/analyses/{analysis_id}/findings.csv")
def findings_csv(
    analysis_id: str,
    store: StoreDep,
    category: Category | None = None,
    severity: Severity | None = None,
    section_id: str | None = None,
    confidence: Confidence | None = None,
    status: Status | None = None,
) -> Response:
    findings = _filtered(store, analysis_id, category, severity, section_id, confidence, status)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "番号",
            "種類",
            "内容",
            "重大度",
            "確度",
            "ページ",
            "該当箇所",
            "指摘",
            "理由",
            "改善案",
            "ステータス",
            "却下の理由",
        ]
    )
    status_labels = {"open": "未対応", "accepted": "対応済み", "dismissed": "却下"}
    for f in findings:
        writer.writerow(
            [
                f.finding_id,
                category_label(f.category),
                subcategory_label(f.subcategory),
                severity_label(f.severity),
                ("高" if f.confidence == "high" else "低") if f.voted else "",
                f.page,
                f.quote,
                f.issue,
                f.rationale,
                " / ".join(f.suggestions),
                status_labels[f.status],
                f.dismiss_reason or "",
            ]
        )
    # Excel で文字化けしないよう BOM を付ける
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{analysis_id}_findings.csv"'},
    )


def _diff(store: Store, analysis_id: str) -> tuple[PreviousAnalysis | None, FindingsDiff | None]:
    """同じ論文の前回の完了した解析と、その差分（前回が無ければ None）。"""
    prev_record = store.find_previous(analysis_id)
    if prev_record is None:
        return None, None
    previous_findings = store.get_findings(prev_record.analysis_id)
    prev_counts = count_findings(previous_findings, None)
    previous = PreviousAnalysis(
        analysis_id=prev_record.analysis_id,
        created_at=prev_record.created_at,
        total=prev_counts.total,
        by_severity=prev_counts.by_severity,
    )
    return previous, diff_findings(store.get_findings(analysis_id), previous_findings)


@app.get("/api/analyses/{analysis_id}/diff")
def analysis_diff(analysis_id: str, store: StoreDep) -> AnalysisDiff:
    """前回の解析との差分（解消された指摘・新しい指摘・続いている指摘）。"""
    _analysis_or_404(store, analysis_id)
    previous, diff = _diff(store, analysis_id)
    if previous is None or diff is None:
        return AnalysisDiff(previous=None, resolved=[], new=[], persisting=[])
    return AnalysisDiff(
        previous=previous,
        resolved=diff.resolved,
        new=[f.finding_id for f in diff.new],
        persisting=[
            PersistingPair(finding_id=c.finding_id, previous_finding_id=p.finding_id)
            for c, p in diff.persisting
        ],
    )


@app.get("/api/analyses/{analysis_id}/findings.json")
def findings_json(analysis_id: str, store: StoreDep) -> Response:
    """指摘の一覧の JSON（SPEC 3.4 の形式。却下したものも含め、ステータスと却下の理由を付ける）。"""
    record = _analysis_or_404(store, analysis_id)
    doc = store.get_document(record.doc_id)
    paper = store.get_paper(record.doc_id)
    body = {
        "analysis_id": analysis_id,
        "created_at": record.created_at,
        "paper": {
            "doc_id": record.doc_id,
            "title": doc.title if doc else None,
            "filename": paper.filename if paper else None,
        },
        "findings": [f.model_dump(mode="json") for f in store.get_findings(analysis_id)],
    }
    return Response(
        content=json.dumps(body, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{analysis_id}_findings.json"'},
    )


@app.get("/api/analyses/{analysis_id}/report.txt")
def report_text(analysis_id: str, store: StoreDep) -> Response:
    """主要な問題と件数をまとめたテキストの要約（SPEC 3.5・6.6）。"""
    record = _analysis_or_404(store, analysis_id)
    doc = store.get_document(record.doc_id)
    paper = store.get_paper(record.doc_id)
    findings = store.get_findings(analysis_id)
    previous, diff = _diff(store, analysis_id)
    text = text_report(
        title=doc.title if doc else None,
        filename=paper.filename if paper else None,
        analysis_id=analysis_id,
        created_at=record.created_at,
        counts=count_findings(findings, doc),
        findings=findings,
        previous_total=previous.total if previous else None,
        diff=diff,
    )
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{analysis_id}_report.txt"'},
    )


@app.get("/api/analyses/{analysis_id}/annotated.pdf")
def annotated_pdf(
    analysis_id: str,
    store: StoreDep,
    include_accepted: Annotated[bool, Query(description="対応済みの指摘も書き込む")] = False,
) -> Response:
    """注釈付きPDF。未対応の指摘（と、指定すれば対応済みの指摘）を書き込む。却下した指摘は書き込まない。"""
    analysis = store.get_analysis(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="解析が見つかりません")
    try:
        paper = load_paper(analysis.doc_id)
    except ValueError:
        paper = None
    if paper is None:
        raise HTTPException(status_code=404, detail="論文のファイルが見つかりません")
    statuses = frozenset({"open", "accepted"} if include_accepted else {"open"})
    pdf = annotate_pdf(paper, store.get_findings(analysis_id), statuses=statuses)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{analysis.doc_id}_annotated.pdf"'},
    )


# ---------------------------------------------------------------- 外部通信ログ・承認キュー（gateway）


@app.get("/api/audit-log")
async def audit_log(gateway: GatewayDep, analysis_id: str | None = None) -> AuditLog:
    """学外への要求の記録（検索ゲートウェイの監査ログ。拒否・承認待ちを含む）。"""
    try:
        mode = await gateway.mode()
        entries = await gateway.audit(analysis_id=analysis_id)
    except GatewayError:
        return AuditLog(available=False, entries=[])
    return AuditLog(available=True, mode=mode, entries=entries)


@app.get("/api/gateway/queue")
async def gateway_queue(gateway: GatewayDep) -> list[dict[str, Any]]:
    try:
        return await gateway.queue()
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@app.post("/api/gateway/queue/{request_id}/{action}")
async def gateway_decide(
    request_id: str, action: Literal["approve", "reject"], gateway: GatewayDep
) -> dict[str, Any]:
    try:
        response = await (
            gateway.approve(request_id) if action == "approve" else gateway.reject(request_id)
        )
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return {
        "request_id": response.request_id,
        "decision": response.decision,
        "reason": response.reason,
    }


# ---------------------------------------------------------------- ルール（SPEC 3.2）

MAX_RULE_DOCUMENT_BYTES = 30 * 1024 * 1024


@app.get("/api/rules")
def list_rules(rules: RuleStoreDep, status: RuleStatus | None = None) -> list[Rule]:
    return rules.list_rules(status=status)


@app.patch("/api/rules/{rule_id}")
def update_rule(rule_id: str, update: RuleUpdate, rules: RuleStoreDep) -> Rule:
    """ルールの確認・編集・承認・無効化。組み込みのルールは有効・無効だけを変えられる。"""
    try:
        return rules.update_rule(rule_id, update)
    except RuleNotFoundError:
        raise HTTPException(status_code=404, detail="ルールが見つかりません") from None


@app.post("/api/rules/bulk-status")
def bulk_rule_status(request: BulkRuleStatus, rules: RuleStoreDep) -> list[Rule]:
    """複数のルールをまとめて承認・無効・保留にする（見つからない ID は無視する）。"""
    updated: list[Rule] = []
    for rule_id in request.rule_ids:
        try:
            updated.append(rules.update_rule(rule_id, RuleUpdate(status=request.status)))
        except RuleNotFoundError:
            continue
    return updated


@app.get("/api/rule-documents")
def list_rule_documents(rules: RuleStoreDep) -> list[RuleDocument]:
    return rules.list_documents()


@app.post("/api/rule-documents", status_code=201)
async def upload_rule_document(
    file: UploadFile, rules: RuleStoreDep, queue: QueueDep
) -> RuleDocument:
    """ルール資料（PDF・DOCX・Markdown・テキスト）を登録し、ルールの抽出を始める。資料は data_private に保存する。"""
    filename = os.path.basename(file.filename or "rules.txt")[:255]
    kind = SUFFIXES.get(os.path.splitext(filename)[1].lower())
    if kind is None:
        raise HTTPException(
            status_code=415,
            detail="PDF・Markdown（.md）・テキスト（.txt）のファイルを選んでください",
        )
    data = await file.read(MAX_RULE_DOCUMENT_BYTES + 1)
    if len(data) > MAX_RULE_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="ファイルが大きすぎます（上限 30MB）")
    if not looks_valid(kind, data):
        raise HTTPException(status_code=415, detail="ファイルの形式が拡張子と合いません")
    doc = rules.create_document(filename, kind)
    save_document_file(doc.document_id, kind, data)
    await queue.enqueue_rules(doc.document_id)
    return doc


@app.delete("/api/rule-documents/{document_id}", status_code=204)
def delete_rule_document(document_id: str, rules: RuleStoreDep) -> Response:
    """資料と、そこから抽出したルールを削除する（ほかの資料のルールに統合された出典の記録は残る）。"""
    doc = rules.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="ルール資料が見つかりません")
    if doc.status in ("queued", "running"):
        raise HTTPException(status_code=409, detail="ルールの抽出中は削除できません")
    rules.delete_document(document_id)
    with contextlib.suppress(ValueError):
        delete_rule_file(document_id, doc.kind)
    return Response(status_code=204)


# ---------------------------------------------------------------- 専門知識（SPEC 3.1〜3.3）
# すべて、この MongAI が扱う研究室（current_lab_id）のデータだけを読み書きする


@app.get("/api/lab/documents")
def list_lab_documents(lab: LabStoreDep) -> list[LabDocument]:
    return lab.list_documents(current_lab_id())


@app.post("/api/lab/documents", status_code=201)
async def upload_lab_document(file: UploadFile, lab: LabStoreDep, queue: QueueDep) -> LabDocument:
    """過去の査読コメント・回答書・添削メモ（PDF・DOCX・Markdown・テキスト）を登録し、取り込みを始める。"""
    filename = os.path.basename(file.filename or "notes.txt")[:255]
    kind = SUFFIXES.get(os.path.splitext(filename)[1].lower())
    if kind is None:
        raise HTTPException(
            status_code=415,
            detail="PDF・Word（.docx）・Markdown（.md）・テキスト（.txt）のファイルを選んでください",
        )
    data = await file.read(MAX_RULE_DOCUMENT_BYTES + 1)
    if len(data) > MAX_RULE_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="ファイルが大きすぎます（上限 30MB）")
    if not looks_valid(kind, data):
        raise HTTPException(status_code=415, detail="ファイルの形式が拡張子と合いません")
    doc = lab.create_document(current_lab_id(), filename, kind)
    save_lab_file(doc.document_id, kind, data)
    await queue.enqueue_lab(doc.document_id)
    return doc


@app.delete("/api/lab/documents/{document_id}", status_code=204)
def delete_lab_document(document_id: str, lab: LabStoreDep, rules: RuleStoreDep) -> Response:
    """資料と、そこから取り出した過去の指摘・研究室のルールを削除する。"""
    doc = lab.get_document(current_lab_id(), document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="専門知識の資料が見つかりません")
    if doc.status in ("queued", "running"):
        raise HTTPException(status_code=409, detail="取り込み中は削除できません")
    lab.delete_document(current_lab_id(), document_id)
    rules.delete_document(document_id)  # この資料から取り出したルール
    with contextlib.suppress(ValueError):
        delete_lab_file(document_id, doc.kind)
    return Response(status_code=204)


@app.get("/api/lab/comments")
def list_lab_comments(lab: LabStoreDep) -> list[LabComment]:
    return lab.list_comments(current_lab_id())


@app.delete("/api/lab/comments/{comment_id}", status_code=204)
def delete_lab_comment(comment_id: str, lab: LabStoreDep) -> Response:
    if not lab.delete_comment(current_lab_id(), comment_id):
        raise HTTPException(status_code=404, detail="過去の指摘が見つかりません")
    return Response(status_code=204)
