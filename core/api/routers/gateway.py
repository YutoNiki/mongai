"""外部通信ログと承認キュー（検索ゲートウェイの中継）。"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from core.api.dependencies import GatewayDep, StoreDep
from core.api.schemas import AuditLog
from core.gateway_client import GatewayError

router = APIRouter()


@router.get("/audit-log")
async def audit_log(gateway: GatewayDep, analysis_id: str | None = None) -> AuditLog:
    """学外への要求の記録（検索ゲートウェイの監査ログ。拒否・承認待ちを含む）。"""
    try:
        mode = await gateway.mode()
        entries = await gateway.audit(analysis_id=analysis_id)
    except GatewayError:
        return AuditLog(available=False, entries=[])
    return AuditLog(available=True, mode=mode, entries=entries)


@router.get("/gateway/queue")
async def gateway_queue(gateway: GatewayDep) -> list[dict[str, Any]]:
    try:
        return await gateway.queue()
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@router.post("/gateway/queue/{request_id}/{action}")
async def gateway_decide(
    request_id: str, action: Literal["approve", "reject"], gateway: GatewayDep, store: StoreDep
) -> dict[str, Any]:
    try:
        response = await (
            gateway.approve(request_id) if action == "approve" else gateway.reject(request_id)
        )
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    # 用語の照会なら、その要求を積んだ解析の記録に結果を反映する（ダッシュボードに表示する）
    found = response.response.term if response.response else None
    if action == "reject":
        store.decide_term_lookup(request_id, "rejected")
    elif response.decision == "sent":
        store.decide_term_lookup(
            request_id,
            "approved",
            matched=found.term if found else None,
            description=found.description if found else None,
            source=found.source if found else None,
        )
    elif response.decision == "failed":
        store.decide_term_lookup(request_id, "failed")
    return {
        "request_id": response.request_id,
        "decision": response.decision,
        "reason": response.reason,
    }
