"""外部通信ログと承認キュー（検索ゲートウェイの中継）。

gateway には主体の内部ユーザーID（owner_id）を渡し、本人の解析が出した要求だけを読み書きする。
ゲストは共通デモの解析の記録だけを読める。
"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from core.api.dependencies import GatewayDep, PrincipalDep, StoreDep, UserDep
from core.api.queries import is_demo
from core.api.schemas import AuditLog
from core.auth.principal import AuthenticatedUser, viewer_of
from core.gateway_client import GatewayError

router = APIRouter()


@router.get("/audit-log")
async def audit_log(
    gateway: GatewayDep, store: StoreDep, principal: PrincipalDep, analysis_id: str | None = None
) -> AuditLog:
    """学外への要求の記録（検索ゲートウェイの監査ログ。拒否・承認待ちを含む）。"""
    owner_id: str | None
    if analysis_id is None:
        if not isinstance(principal, AuthenticatedUser):
            raise HTTPException(status_code=401, detail="login_required")
        owner_id = principal.user_id  # 自分の解析の記録すべて
    else:
        record = store.get_analysis_for(analysis_id, viewer_of(principal))
        if record is None:
            # ゲストには、デモ以外の解析の有無を明かさない
            if not isinstance(principal, AuthenticatedUser):
                raise HTTPException(status_code=401, detail="login_required")
            raise HTTPException(status_code=404, detail="解析が見つかりません")
        owner_id = None if is_demo(record) else record.owner_user_id
    try:
        mode = await gateway.mode()
        entries = await gateway.audit(analysis_id=analysis_id, owner_id=owner_id)
    except GatewayError:
        return AuditLog(available=False, entries=[])
    return AuditLog(available=True, mode=mode, entries=entries)


@router.get("/gateway/queue")
async def gateway_queue(gateway: GatewayDep, user: UserDep) -> list[dict[str, Any]]:
    try:
        return await gateway.queue(owner_id=user.user_id)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@router.post("/gateway/queue/{request_id}/{action}")
async def gateway_decide(
    request_id: str,
    action: Literal["approve", "reject"],
    gateway: GatewayDep,
    store: StoreDep,
    user: UserDep,
) -> dict[str, Any]:
    owner = user.user_id
    # 本人の解析が積んだ要求だけを承認・却下できる（他人の要求は 404）
    try:
        pending = await gateway.queue(owner_id=owner)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    if not any(item.get("request_id") == request_id for item in pending):
        raise HTTPException(status_code=404, detail="承認待ちの要求が見つかりません")
    try:
        response = await (
            gateway.approve(request_id, owner_id=owner)
            if action == "approve"
            else gateway.reject(request_id, owner_id=owner)
        )
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    # 用語の照会なら、その要求を積んだ解析の記録に結果を反映する（ダッシュボードに表示する）
    found = response.response.term if response.response else None
    if action == "reject":
        store.decide_term_lookup(request_id, "rejected", owner_user_id=owner)
    elif response.decision == "sent":
        store.decide_term_lookup(
            request_id,
            "approved",
            owner_user_id=owner,
            matched=found.term if found else None,
            description=found.description if found else None,
            source=found.source if found else None,
        )
    elif response.decision == "failed":
        store.decide_term_lookup(request_id, "failed", owner_user_id=owner)
    return {
        "request_id": response.request_id,
        "decision": response.decision,
        "reason": response.reason,
    }
