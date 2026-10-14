"""core の FastAPI アプリ。`web`（nginx）から `/api` 以下が中継される。

認証は無い（web は 127.0.0.1 にだけ公開し、SSH のポートフォワード等で利用する前提）。
エンドポイントは `core.api.routers` にリソースごとに分け、依存性は `core.api.dependencies` にまとめる。
"""

from fastapi import FastAPI

from core.api.dependencies import (
    ArqQueue,
    JobQueue,
    get_gateway,
    get_lab_store,
    get_queue,
    get_rule_store,
    get_store,
)
from core.api.routers import analyses, documents, exports, findings, gateway, lab, rules, usage
from core.logging_config import configure_logging

# テスト・既存のコードが `core.api.main` から参照する名前（dependency_overrides のキーと同じ関数）
__all__ = [
    "ArqQueue",
    "JobQueue",
    "app",
    "get_gateway",
    "get_lab_store",
    "get_queue",
    "get_rule_store",
    "get_store",
]

configure_logging()

app = FastAPI(title="MongAI core", docs_url="/api/docs", openapi_url="/api/openapi.json")


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
