"""指摘の一覧（絞り込み）とステータスの変更。"""

from fastapi import APIRouter, HTTPException

from core.analyze.models import Finding
from core.api.dependencies import StoreDep
from core.api.queries import Category, Confidence, Severity, Status, filtered_findings
from core.api.schemas import StatusUpdate
from core.store import FindingNotFoundError

router = APIRouter()


@router.get("/analyses/{analysis_id}/findings")
def list_findings(
    analysis_id: str,
    store: StoreDep,
    category: Category | None = None,
    severity: Severity | None = None,
    section_id: str | None = None,
    confidence: Confidence | None = None,
    status: Status | None = None,
) -> list[Finding]:
    return filtered_findings(store, analysis_id, category, severity, section_id, confidence, status)


@router.patch("/analyses/{analysis_id}/findings/{finding_id}")
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
