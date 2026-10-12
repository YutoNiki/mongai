"""core の FastAPI アプリ。`web`（nginx）から `/api` 以下が中継される。"""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response

from core.annotate.pdf import annotate_pdf
from core.logging_config import configure_logging
from core.store import Store
from core.store.files import load_paper

configure_logging()

app = FastAPI(title="MonGAI core", docs_url="/api/docs", openapi_url="/api/openapi.json")


def get_store() -> Store:
    return Store()


StoreDep = Annotated[Store, Depends(get_store)]


@app.get("/api/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/analyses/{analysis_id}/annotated.pdf")
def annotated_pdf(
    analysis_id: str,
    store: StoreDep,
    include_accepted: Annotated[bool, Query(description="対応済みの指摘も書き込む")] = False,
) -> Response:
    """注釈付きPDF。未対応の指摘（と、指定すれば対応済みの指摘）を書き込む。却下した指摘は書き込まない。"""
    analysis = store.get_analysis(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="解析が見つかりません")
    try:
        paper = load_paper(analysis.doc_id)
    except ValueError:
        paper = None
    if paper is None:
        raise HTTPException(status_code=404, detail="論文のファイルが見つかりません")
    statuses = frozenset({"open", "accepted"} if include_accepted else {"open"})
    pdf = annotate_pdf(paper, store.get_findings(analysis_id), statuses=statuses)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{analysis.doc_id}_annotated.pdf"'},
    )
