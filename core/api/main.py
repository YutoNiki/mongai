"""core の FastAPI アプリ。`web`（nginx）から `/api` 以下が中継される。

リクエストの主体は、web が認証サービスに問い合わせて付ける署名つきの主張で決まる（無ければゲスト）。
エンドポイントは `core.api.routers` にリソースごとに分け、依存性は `core.api.dependencies` にまとめる。
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from core.api.dependencies import (
    ArqQueue,
    JobQueue,
    get_gateway,
    get_lab_store,
    get_principal,
    get_queue,
    get_rule_store,
    get_store,
    get_user_directory,
    get_verifier,
)
from core.api.routers import analyses, documents, exports, findings, gateway, lab, rules, usage
from core.logging_config import configure_logging
from core.store.db import DEFAULT_DB_PATH
from core.store.schema import ensure_schema

# テスト・既存のコードが `core.api.main` から参照する名前（dependency_overrides のキーと同じ関数）
__all__ = [
    "ArqQueue",
    "JobQueue",
    "app",
    "get_gateway",
    "get_lab_store",
    "get_principal",
    "get_queue",
    "get_rule_store",
    "get_store",
    "get_user_directory",
    "get_verifier",
]

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # データベースが古い版（移行前）なら起動しない。所有者の無いデータを誤って見せないため
    ensure_schema(Path(os.environ.get("MONGAI_DB_PATH") or DEFAULT_DB_PATH))
    # 本番の構成で開発用の鍵が設定されていたら起動しない
    get_verifier().check_configuration()
    yield


app = FastAPI(
    title="SI-NSA core", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan
)


@app.get("/api/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(documents.router, prefix="/api")
app.include_router(usage.router, prefix="/api")
app.include_router(analyses.router, prefix="/api")
app.include_router(findings.router, prefix="/api")
app.include_router(exports.router, prefix="/api")
app.include_router(gateway.router, prefix="/api")
app.include_router(rules.router, prefix="/api")
app.include_router(lab.router, prefix="/api")
