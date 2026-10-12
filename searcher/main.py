"""検索エージェント。論文データを持たず、許可された書誌APIにだけアクセスする（M7 で実装）。"""

from fastapi import FastAPI

app = FastAPI(title="MonGAI searcher", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
