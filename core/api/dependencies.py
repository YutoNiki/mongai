"""API の依存性（リクエストの主体・ストア・ジョブのキュー・検索ゲートウェイ）。

ルーターはここで定義した `*Dep` を引数の型に使う。テストでは `app.dependency_overrides` で
`get_*` を差し替える（`core.api.main` からも同じ関数を参照できる）。主体は `get_principal` を
差し替える（例：`lambda: AuthenticatedUser("U-…")`、`lambda: Guest()`）。

すべてのエンドポイント（/api/healthz を除く）は PrincipalDep か UserDep のどちらかを使う
（tests/unit/test_api_routes.py で確かめる）。
"""

import os
from typing import Annotated, Protocol
from urllib.parse import urlsplit

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Depends, HTTPException, Request

from core.auth.assertion import ASSERTION_HEADER, AssertionVerifier, InvalidAssertion
from core.auth.directory import UserDirectory
from core.auth.principal import AuthenticatedUser, Guest, Principal
from core.gateway_client import GatewayClient
from core.lab.store import LabStore
from core.rules.store import RuleStore
from core.store import Store


class JobQueue(Protocol):
    async def enqueue(self, analysis_id: str) -> None: ...

    async def enqueue_rules(self, document_id: str) -> None: ...

    async def enqueue_lab(self, document_id: str) -> None: ...

    async def enqueue_instructions(self, draft_id: str) -> None: ...


JOB_SUBMISSION_ERROR = "処理を開始できませんでした。しばらく待ってから、もう一度お試しください。"


def job_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503, detail=JOB_SUBMISSION_ERROR, headers={"Retry-After": "30"}
    )


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

    async def enqueue_instructions(self, draft_id: str) -> None:
        pool = await self._redis()
        await pool.enqueue_job("run_instruction_extraction", draft_id, _job_id=draft_id)


_queue = ArqQueue()
_verifier = AssertionVerifier()
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_verifier() -> AssertionVerifier:
    return _verifier


def get_user_directory() -> UserDirectory:
    return UserDirectory()


def get_principal(
    request: Request,
    verifier: Annotated[AssertionVerifier, Depends(get_verifier)],
    directory: Annotated[UserDirectory, Depends(get_user_directory)],
) -> Principal:
    """主張が無ければゲスト。主張があって無効なら 401（ゲストとして扱わない）。"""
    token = request.headers.get(ASSERTION_HEADER)
    if not token:
        return Guest()
    try:
        return AuthenticatedUser(user_id=directory.resolve(verifier.verify(token)))
    except InvalidAssertion:
        raise HTTPException(status_code=401, detail="invalid_session") from None


def _check_same_origin(request: Request) -> None:
    """書き込みの要求が、ほかのサイトから送られたものでないことを確かめる（CSRF 対策）。"""
    if request.method in _SAFE_METHODS:
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        raise HTTPException(status_code=403, detail="ほかのサイトからの要求は受け付けません")
    origin = request.headers.get("origin")
    host = urlsplit("//" + request.headers.get("host", "")).hostname
    if origin is not None and urlsplit(origin).hostname != host:
        raise HTTPException(status_code=403, detail="ほかのサイトからの要求は受け付けません")


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_user(request: Request, principal: PrincipalDep) -> AuthenticatedUser:
    if not isinstance(principal, AuthenticatedUser):
        raise HTTPException(status_code=401, detail="login_required")
    _check_same_origin(request)
    return principal


UserDep = Annotated[AuthenticatedUser, Depends(require_user)]


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
