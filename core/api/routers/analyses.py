"""解析の開始・一覧・集計・用語の照会・前回との差分。"""

from fastapi import APIRouter, HTTPException

from core.analyze.pipeline import AnalysisSettings
from core.api.dependencies import GatewayDep, QueueDep, StoreDep
from core.api.queries import analysis_or_404, previous_and_diff
from core.api.schemas import (
    AnalysisDiff,
    AnalysisListItem,
    AnalysisSummary,
    CreateAnalysisRequest,
    DocumentInfo,
    PersistingPair,
)
from core.api.summary import count_findings
from core.gateway_client import GatewayClient, GatewayError
from core.llm.config import get_models_config
from core.sample import is_sample
from core.store import Store, TermLookupRecord

# ダッシュボードのメタ情報に表示する役割
DISPLAYED_ROLES = (
    "structure_extraction",
    "logic_check",
    "second_opinion",
    "citation_support",
    "suggestion",
)

router = APIRouter()


@router.post("/analyses", status_code=201)
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


@router.get("/analyses")
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
    record = analysis_or_404(store, analysis_id)
    doc = store.get_document(record.doc_id)
    counts = count_findings(store.get_findings(analysis_id), doc)
    previous, _ = previous_and_diff(store, analysis_id)
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


@router.get("/analyses/{analysis_id}")
async def get_analysis(analysis_id: str, store: StoreDep, gateway: GatewayDep) -> AnalysisSummary:
    return await summarize(store, analysis_id, gateway)


@router.get("/analyses/{analysis_id}/terms")
def analysis_terms(analysis_id: str, store: StoreDep) -> list[TermLookupRecord]:
    """この解析で照会した未知の用語と、その結果（承認後は学外の辞書の説明）。"""
    analysis_or_404(store, analysis_id)
    return store.get_term_lookups(analysis_id)


@router.get("/analyses/{analysis_id}/diff")
def analysis_diff(analysis_id: str, store: StoreDep) -> AnalysisDiff:
    """前回の解析との差分（解消された指摘・新しい指摘・続いている指摘）。"""
    analysis_or_404(store, analysis_id)
    previous, diff = previous_and_diff(store, analysis_id)
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
