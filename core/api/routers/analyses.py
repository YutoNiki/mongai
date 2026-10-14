"""解析の開始・一覧・集計・用語の照会・前回との差分。"""

from fastapi import APIRouter, HTTPException

from core.analyze.pipeline import AnalysisSettings
from core.api.dependencies import GatewayDep, PrincipalDep, QueueDep, StoreDep, UserDep
from core.api.queries import is_demo, owned_paper, previous_and_diff, readable_analysis
from core.api.schemas import (
    AnalysisDiff,
    AnalysisListItem,
    AnalysisSummary,
    CreateAnalysisRequest,
    DocumentInfo,
    PersistingPair,
)
from core.api.summary import count_findings
from core.auth.principal import viewer_of
from core.gateway_client import GatewayClient, GatewayError
from core.llm.config import get_models_config
from core.store import AnalysisRecord, Store, TermLookupRecord

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
    request: CreateAnalysisRequest,
    store: StoreDep,
    queue: QueueDep,
    gateway: GatewayDep,
    user: UserDep,
) -> AnalysisSummary:
    try:
        owned_paper(store, user, request.doc_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=403, detail="デモの論文は解析できません") from None
        raise
    settings = AnalysisSettings(output_lang=request.output_lang)
    analysis_id = store.create_analysis(
        request.doc_id, owner_user_id=user.user_id, kind="logic", settings=settings, status="queued"
    )
    await queue.enqueue(analysis_id)
    record = store.get_analysis(analysis_id)
    assert record is not None
    return await summarize(store, record, gateway)


@router.get("/analyses")
def list_analyses(store: StoreDep, principal: PrincipalDep) -> list[AnalysisListItem]:
    """主体が読める解析（ゲストは共通デモだけ、利用者は自分の解析と共通デモ）。"""
    items = []
    for record in store.list_analyses(viewer_of(principal)):
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
                sample=is_demo(record),
            )
        )
    return items


async def summarize(
    store: Store, record: AnalysisRecord, gateway: GatewayClient
) -> AnalysisSummary:
    """読めることを確かめた解析の集計。"""
    analysis_id = record.analysis_id
    doc = store.get_document(record.doc_id)
    counts = count_findings(store.get_findings(analysis_id), doc)
    previous, _ = previous_and_diff(store, record)
    config = get_models_config()
    usage = store.get_api_usage(analysis_id)
    try:
        external = await gateway.external_count(analysis_id, owner_id=record.owner_user_id)
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
        sample=is_demo(record),
    )


@router.get("/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: str, store: StoreDep, gateway: GatewayDep, principal: PrincipalDep
) -> AnalysisSummary:
    return await summarize(store, readable_analysis(store, principal, analysis_id), gateway)


@router.get("/analyses/{analysis_id}/terms")
def analysis_terms(
    analysis_id: str, store: StoreDep, principal: PrincipalDep
) -> list[TermLookupRecord]:
    """この解析で照会した未知の用語と、その結果（承認後は学外の辞書の説明）。"""
    readable_analysis(store, principal, analysis_id)
    return store.get_term_lookups(analysis_id)


@router.get("/analyses/{analysis_id}/diff")
def analysis_diff(analysis_id: str, store: StoreDep, principal: PrincipalDep) -> AnalysisDiff:
    """前回の解析との差分（解消された指摘・新しい指摘・続いている指摘）。"""
    record = readable_analysis(store, principal, analysis_id)
    previous, diff = previous_and_diff(store, record)
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
