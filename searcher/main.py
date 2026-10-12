"""検索エージェント。論文データを持たず、許可された書誌APIにだけアクセスする（SPEC 2.4）。

入力は gateway で検証済みの要求だけだが、ここでも同じスキーマで検証し直す。
どの文献が一致するかの判断は core 側で行う（ここでは候補を返すだけ。おいとまAPIは使わない）。
"""

import asyncio
import logging
import os
from collections.abc import Awaitable

import httpx
from fastapi import FastAPI

from searcher import apis
from shared.schemas import (
    BibLookup,
    BibResult,
    ExternalRequest,
    OATextFetch,
    SearchResponse,
    TermLookup,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("MONGAI_LOG_LEVEL", "INFO"))
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(title="MonGAI searcher", docs_url=None, redoc_url=None, openapi_url=None)
MAX_CANDIDATES = 20


def http_client() -> httpx.AsyncClient:
    # HTTPS_PROXY（egress_proxy）を使う。許可リスト外のホストにはプロキシが接続させない
    return httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=10.0),
        headers={"User-Agent": apis.USER_AGENT},
        follow_redirects=False,
        trust_env=True,
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _gather(
    calls: dict[str, Awaitable[list[BibResult]]],
) -> tuple[list[BibResult], list[str]]:
    results: list[BibResult] = []
    errors: list[str] = []
    outcomes = await asyncio.gather(*calls.values(), return_exceptions=True)
    for name, outcome in zip(calls, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            errors.append(f"{name}: {type(outcome).__name__}")
        else:
            results.extend(outcome)
    return results, errors


def dedupe(results: list[BibResult]) -> list[BibResult]:
    """同じデータベース内の重複だけを除く（別のデータベースが同じ文献を示すことは、一致の根拠として残す）。"""
    seen: set[str] = set()
    unique: list[BibResult] = []
    for r in results:
        key = r.source + "|" + ((r.doi or "").lower() or f"{(r.title or '').lower()}|{r.year}")
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique[:MAX_CANDIDATES]


async def bib_lookup(http: httpx.AsyncClient, req: BibLookup) -> SearchResponse:
    calls: dict[str, Awaitable[list[BibResult]]] = {}
    if req.doi:
        calls["crossref"] = apis.crossref_doi(http, req.doi)
        calls["openalex"] = apis.openalex_doi(http, req.doi)
    if req.title:
        # 日本語の文献は CiNii・J-STAGE の方が見つかりやすいので、候補の先頭に並べる
        if apis.has_cjk(req.title):
            calls["jstage"] = apis.jstage_search(http, req.title)
            calls["cinii"] = apis.cinii_search(http, req.title)
        calls["crossref_search"] = apis.crossref_search(http, req.title, req.first_author, req.year)
        calls["openalex_search"] = apis.openalex_search(http, req.title)
    results, errors = await _gather(calls)
    return SearchResponse(results=dedupe(results), errors=errors)


async def oa_fetch(http: httpx.AsyncClient, req: OATextFetch) -> SearchResponse:
    errors: list[str] = []
    for name, call in (
        ("openalex", apis.openalex_doi),
        ("semanticscholar", apis.semantic_scholar_doi),
        ("arxiv", apis.arxiv_doi),
    ):
        try:
            found = [r for r in await call(http, req.doi) if r.abstract]
        except httpx.HTTPError as exc:
            errors.append(f"{name}: {type(exc).__name__}")
            continue
        if found:
            return SearchResponse(results=found[:1], errors=errors)
    return SearchResponse(results=[], errors=errors)


async def term_lookup(http: httpx.AsyncClient, req: TermLookup) -> SearchResponse:
    try:
        return SearchResponse(term=await apis.openalex_topic(http, req.term))
    except httpx.HTTPError as exc:
        return SearchResponse(errors=[f"openalex: {type(exc).__name__}"])


@app.post("/search")
async def search(request: ExternalRequest) -> SearchResponse:
    async with http_client() as http:
        if isinstance(request, BibLookup):
            return await bib_lookup(http, request)
        if isinstance(request, OATextFetch):
            return await oa_fetch(http, request)
        return await term_lookup(http, request)
