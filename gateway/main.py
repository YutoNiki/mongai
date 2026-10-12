"""検索ゲートウェイ。core → searcher の要求を検証・監査する（M7 で実装）。"""

from fastapi import FastAPI

app = FastAPI(title="MonGAI gateway", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
