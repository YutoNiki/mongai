"""検索ゲートウェイ。core → searcher の要求を検証・監査し、許可したものだけ searcher に送る（SPEC 2.3）。"""

import logging
import os
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import TypeAdapter, ValidationError

from gateway.audit import AuditEntry, AuditStore, QueueItem, new_request_id
from gateway.config import GatewayConfig, get_config
from gateway.manuscripts import EmbeddingClient, ManuscriptGuard, ManuscriptRegistry
from gateway.policy import check
from shared.schemas import (
    ExternalRequest,
    GatewayRequest,
    GatewayResponse,
    SearchResponse,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("MONGAI_LOG_LEVEL", "INFO"))
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(title="SI-NSA gateway", docs_url=None, redoc_url=None, openapi_url=None)

_registry = ManuscriptRegistry()
_store: AuditStore | None = None
_request_adapter: TypeAdapter[ExternalRequest] = TypeAdapter(ExternalRequest)


def get_store() -> AuditStore:
    global _store
    if _store is None:
        _store = AuditStore()
    return _store


def get_registry() -> ManuscriptRegistry:
    return _registry


async def embed(texts: list[str]) -> list[list[float]]:
    return await EmbeddingClient(get_config().leak_check.embedding_role).embed(texts)


async def send_to_searcher(request: dict[str, Any]) -> SearchResponse:
    url = os.environ.get("SEARCHER_URL", "http://searcher:8000")
    async with httpx.AsyncClient(timeout=90, trust_env=False) as http:
        response = await http.post(f"{url}/search", json=request)
        response.raise_for_status()
        return SearchResponse.model_validate(response.json())


StoreDep = Annotated[AuditStore, Depends(get_store)]
RegistryDep = Annotated[ManuscriptRegistry, Depends(get_registry)]
ConfigDep = Annotated[GatewayConfig, Depends(get_config)]


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/mode")
def mode(config: ConfigDep) -> dict[str, str]:
    return {"mode": config.mode}


@app.post("/manuscripts", status_code=204)
async def register_manuscript(guard: ManuscriptGuard, registry: RegistryDep) -> None:
    vectors = await embed(guard.novelty_claims) if guard.novelty_claims else []
    registry.register(guard, vectors)


@app.delete("/manuscripts/{doc_id}", status_code=204)
def remove_manuscript(doc_id: str, registry: RegistryDep) -> None:
    registry.remove(doc_id)


def _error_kind(exc: ValidationError) -> str:
    # 値は記録しない（原稿の内容が混ざっている可能性があるため）。位置と種類だけ
    return ";".join(f"{'.'.join(str(p) for p in e['loc'])}:{e['type']}" for e in exc.errors()[:5])


async def _send(
    store: AuditStore,
    request_id: str,
    envelope: GatewayRequest,
    request: dict[str, Any],
    decision: str,
) -> GatewayResponse:
    try:
        response = await send_to_searcher(request)
    except httpx.HTTPError as exc:
        store.record(
            request_id=request_id,
            analysis_id=envelope.analysis_id,
            doc_id=envelope.doc_id,
            kind=request["kind"],
            request=request,
            decision="failed",
            reason=type(exc).__name__,
        )
        return GatewayResponse(
            request_id=request_id, decision="failed", reason="検索に失敗しました"
        )
    # 用語が見つからなかった結果はキャッシュしない（辞書が増えたら、次の照会で見つかるようにする）
    if not (request["kind"] == "term_lookup" and response.term is None):
        store.store_cache(request, response.model_dump_json())
    store.record(
        request_id=request_id,
        analysis_id=envelope.analysis_id,
        doc_id=envelope.doc_id,
        kind=request["kind"],
        request=request,
        decision="approved" if decision == "approved" else "sent",
    )
    return GatewayResponse(request_id=request_id, decision="sent", response=response)


@app.post("/requests")
async def submit(
    raw: Request, store: StoreDep, registry: RegistryDep, config: ConfigDep
) -> GatewayResponse:
    request_id = new_request_id()
    try:
        body = await raw.json()
    except ValueError:
        body = None
    analysis_id = body.get("analysis_id") if isinstance(body, dict) else None
    doc_id = body.get("doc_id") if isinstance(body, dict) else None
    try:
        envelope = GatewayRequest.model_validate(body)
    except ValidationError as exc:
        reason = f"スキーマ違反（{_error_kind(exc)}）"
        store.record(
            request_id=request_id,
            analysis_id=str(analysis_id)[:20] if analysis_id else None,
            doc_id=str(doc_id)[:20] if doc_id else None,
            kind=None,
            request="（スキーマ違反のため内容は記録しない）",
            decision="blocked",
            reason=reason,
        )
        raise HTTPException(
            status_code=422,
            detail=GatewayResponse(
                request_id=request_id, decision="blocked", reason=reason
            ).model_dump(),
        ) from None

    request = envelope.request.model_dump()
    common = {
        "request_id": request_id,
        "analysis_id": envelope.analysis_id,
        "doc_id": envelope.doc_id,
        "kind": request["kind"],
        "request": request,
    }

    if config.mode == "offline":
        store.record(**common, decision="blocked", reason="offline モードのため送信しません")
        return GatewayResponse(request_id=request_id, decision="blocked", reason="offline モード")

    verdict = await check(
        envelope.request,
        registry.get(envelope.doc_id),
        config,
        sent_count=store.sent_count(envelope.doc_id),
        embed=embed,
    )
    if not verdict.ok:
        store.record(**common, decision="blocked", reason=verdict.reason)
        return GatewayResponse(request_id=request_id, decision="blocked", reason=verdict.reason)

    cached = store.cached(request)
    if cached is not None:
        store.record(**common, decision="cached")
        return GatewayResponse(
            request_id=request_id,
            decision="cached",
            response=SearchResponse.model_validate_json(cached),
        )

    needs_approval = config.mode == "strict" or request["kind"] == "term_lookup"
    if needs_approval:
        existing = store.pending_for(request)
        if existing is None:
            store.enqueue(request_id, envelope.analysis_id, envelope.doc_id, request)
        store.record(**common, decision="pending", reason="承認待ち")
        return GatewayResponse(
            request_id=existing or request_id, decision="pending", reason="承認待ち"
        )

    return await _send(store, request_id, envelope, request, "sent")


@app.get("/queue")
def queue(store: StoreDep, status: str | None = "pending") -> list[QueueItem]:
    return store.queue(status or None)


@app.post("/queue/{request_id}/approve")
async def approve(request_id: str, store: StoreDep, config: ConfigDep) -> GatewayResponse:
    item = store.get_queue_item(request_id)
    if item is None or item.status != "pending":
        raise HTTPException(status_code=404, detail="承認待ちの要求が見つかりません")
    if config.mode == "offline":
        raise HTTPException(status_code=409, detail="offline モードでは送信できません")
    store.decide(request_id, "approved")
    envelope = GatewayRequest.model_validate(
        {"analysis_id": item.analysis_id, "doc_id": item.doc_id, "request": item.request}
    )
    return await _send(store, request_id, envelope, item.request, "approved")


@app.post("/queue/{request_id}/reject")
def reject(request_id: str, store: StoreDep) -> GatewayResponse:
    item = store.get_queue_item(request_id)
    if item is None or item.status != "pending":
        raise HTTPException(status_code=404, detail="承認待ちの要求が見つかりません")
    store.decide(request_id, "rejected")
    store.record(
        request_id=request_id,
        analysis_id=item.analysis_id,
        doc_id=item.doc_id,
        kind=item.kind,
        request=item.request,
        decision="rejected",
        reason="利用者が却下しました",
    )
    return GatewayResponse(request_id=request_id, decision="rejected")


@app.get("/audit")
def audit(store: StoreDep, analysis_id: str | None = None, limit: int = 500) -> list[AuditEntry]:
    return store.entries(analysis_id=analysis_id, limit=min(limit, 5000))


@app.get("/audit/count")
def audit_count(store: StoreDep, analysis_id: str) -> dict[str, int]:
    return {"count": store.external_count(analysis_id)}
