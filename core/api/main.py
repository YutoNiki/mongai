"""core の FastAPI アプリ。`web`（nginx）から `/api` 以下が中継される。

認証は無い（web は 127.0.0.1 にだけ公開し、SSH のポートフォワード等で利用する前提）。
"""

import csv
import io
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
    AnalysisListItem,
    AnalysisSummary,
    AuditLog,
    CreateAnalysisRequest,
    DocumentInfo,
    PreviousAnalysis,
    StatusUpdate,
    UploadResponse,
)
from core.api.summary import count_findings
from core.gateway_client import GatewayClient, GatewayError
from core.llm.config import get_models_config
from core.logging_config import configure_logging
from core.store import DailyUsage, FindingNotFoundError, PaperBusyError, Store, today_jst
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


class ArqQueue:
    """解析ジョブを Redis（arq）に積む。"""

    def __init__(self) -> None:
        self._pool: ArqRedis | None = None

    async def enqueue(self, analysis_id: str) -> None:
        if self._pool is None:
            dsn = os.environ.get("REDIS_URL", "redis://redis:6379/0")
            self._pool = await create_pool(RedisSettings.from_dsn(dsn))
        await self._pool.enqueue_job("run_analysis", analysis_id, _job_id=analysis_id)


_queue = ArqQueue()


def get_store() -> Store:
    return Store()


def get_queue() -> JobQueue:
    return _queue


def get_gateway() -> GatewayClient:
    return GatewayClient()


StoreDep = Annotated[Store, Depends(get_store)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
GatewayDep = Annotated[GatewayClient, Depends(get_gateway)]


def _analysis_or_404(store: Store, analysis_id: str) -> None:
    if store.get_analysis(analysis_id) is None:
        raise HTTPException(status_code=404, detail="解析が見つかりません")


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
            )
        )
    return items


async def summarize(store: Store, analysis_id: str, gateway: GatewayClient) -> AnalysisSummary:
    record = store.get_analysis(analysis_id)
    if record is None:
        raise HTTPException(status_code=404, detail="解析が見つかりません")
    doc = store.get_document(record.doc_id)
    counts = count_findings(store.get_findings(analysis_id), doc)
    previous = None
    prev_record = store.find_previous(analysis_id)
    if prev_record is not None:
        prev_counts = count_findings(store.get_findings(prev_record.analysis_id), None)
        previous = PreviousAnalysis(
            analysis_id=prev_record.analysis_id,
            created_at=prev_record.created_at,
            total=prev_counts.total,
            by_severity=prev_counts.by_severity,
        )
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
    return store.get_findings(
        analysis_id,
        category=category,
        severity=severity,
        section_id=section_id,
        confidence=confidence,
        status=status,
    )


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
                "高" if f.confidence == "high" else "低",
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
