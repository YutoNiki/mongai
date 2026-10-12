"""core の FastAPI アプリ。`web`（nginx）から `/api` 以下が中継される。"""

from fastapi import FastAPI

from core.logging_config import configure_logging

configure_logging()

app = FastAPI(title="MonGAI core", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.get("/api/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
