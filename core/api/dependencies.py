"""API の依存性（ストア・ジョブのキュー・検索ゲートウェイ）。

ルーターはここで定義した `*Dep` を引数の型に使う。テストでは `app.dependency_overrides` で
`get_*` を差し替える（`core.api.main` からも同じ関数を参照できる）。
"""

import os
from typing import Annotated, Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Depends

from core.gateway_client import GatewayClient
from core.lab.store import LabStore
from core.rules.store import RuleStore
from core.store import Store


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
