"""許可された書誌API（egress_proxy の許可リスト）への問い合わせと、応答の変換。

外部の応答はデータとして扱う。HTML のタグは除き、長さを制限してから返す。
"""

import html
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx

from shared.schemas import BibResult, TermResult

USER_AGENT = "MongAI/0.1 (pre-submission self-review tool)"
MAX_TEXT = 4000
_TAG = re.compile(r"<[^>]+>")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


def has_cjk(text: str) -> bool:
    return _CJK.search(text) is not None


def clean(text: Any, limit: int = MAX_TEXT) -> str | None:
    if not text:
        return None
    value = html.unescape(_TAG.sub(" ", str(text)))
    value = " ".join(value.split())
    return value[:limit] or None


def _year(value: Any) -> int | None:
    match = re.search(r"(1[89]|20)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None


def _pages(first: Any, last: Any) -> str | None:
    if first and last:
        return f"{first}-{last}"
    return clean(first, 30) if first else None


def _bare_doi(value: Any) -> str | None:
    if not value:
        return None
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", str(value)).strip() or None


# ---------------------------------------------------------------- Crossref


def parse_crossref(item: dict[str, Any]) -> BibResult:
    authors = [
        " ".join(p for p in (a.get("given"), a.get("family")) if p) or str(a.get("name", ""))
        for a in item.get("author", [])
    ]
    date = (item.get("issued") or {}).get("date-parts") or [[None]]
    return BibResult(
        found=True,
        doi=_bare_doi(item.get("DOI")),
        title=clean((item.get("title") or [None])[0], 500),
        authors=[a for a in authors if a][:30],
        year=_year(date[0][0] if date and date[0] else None),
        venue=clean((item.get("container-title") or [None])[0], 300),
        volume=clean(item.get("volume"), 30),
        issue=clean(item.get("issue"), 30),
        pages=clean(item.get("page"), 30),
        source="crossref",
        abstract=clean(item.get("abstract")),
    )


async def crossref_doi(http: httpx.AsyncClient, doi: str) -> list[BibResult]:
    r = await http.get(f"https://api.crossref.org/works/{quote(doi, safe='/')}")
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return [parse_crossref(r.json()["message"])]


async def crossref_search(
    http: httpx.AsyncClient, title: str, author: str | None, year: int | None
) -> list[BibResult]:
    params = {"query.bibliographic": title, "rows": "5"}
    if author:
        params["query.author"] = author
    r = await http.get("https://api.crossref.org/works", params=params)
    r.raise_for_status()
    return [parse_crossref(i) for i in r.json()["message"].get("items", [])]


# ---------------------------------------------------------------- OpenAlex


def openalex_abstract(index: dict[str, list[int]] | None) -> str | None:
    if not index:
        return None
    words: dict[int, str] = {}
    for word, positions in index.items():
        for pos in positions:
            words[pos] = word
    return clean(" ".join(words[i] for i in sorted(words)))


def parse_openalex(item: dict[str, Any]) -> BibResult:
    biblio = item.get("biblio") or {}
    source = ((item.get("primary_location") or {}).get("source") or {}).get("display_name")
    volume = biblio.get("volume")
    return BibResult(
        found=True,
        doi=_bare_doi(item.get("doi")),
        title=clean(item.get("title") or item.get("display_name"), 500),
        authors=[
            str((a.get("author") or {}).get("display_name", ""))
            for a in item.get("authorships", [])
            if (a.get("author") or {}).get("display_name")
        ][:30],
        year=_year(item.get("publication_year")),
        venue=clean(source, 300),
        volume=clean(volume, 30) if volume and any(c.isdigit() for c in str(volume)) else None,
        issue=clean(biblio.get("issue"), 30),
        pages=_pages(biblio.get("first_page"), biblio.get("last_page")),
        source="openalex",
        abstract=openalex_abstract(item.get("abstract_inverted_index")),
    )


async def openalex_doi(http: httpx.AsyncClient, doi: str) -> list[BibResult]:
    r = await http.get(f"https://api.openalex.org/works/doi:{quote(doi, safe='/')}")
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return [parse_openalex(r.json())]


async def openalex_search(http: httpx.AsyncClient, title: str) -> list[BibResult]:
    r = await http.get("https://api.openalex.org/works", params={"search": title, "per-page": "5"})
    r.raise_for_status()
    return [parse_openalex(i) for i in r.json().get("results", [])]


async def openalex_concept(http: httpx.AsyncClient, term: str) -> TermResult | None:
    """用語単位の辞書（OpenAlex の concepts。Wikidata に対応づいた約6.5万語と、その説明）。"""
    r = await http.get(
        "https://api.openalex.org/concepts", params={"search": term, "per-page": "1"}
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        return None
    top = results[0]
    return TermResult(
        term=clean(top.get("display_name"), 200) or term,
        description=clean(top.get("description"), 1000),
        source="openalex",
    )


async def openalex_topic(http: httpx.AsyncClient, term: str) -> TermResult | None:
    r = await http.get("https://api.openalex.org/topics", params={"search": term, "per-page": "1"})
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        return None
    top = results[0]
    return TermResult(
        term=clean(top.get("display_name"), 200) or term,
        description=clean(top.get("description"), 1000),
        source="openalex",
    )


# ---------------------------------------------------------------- Semantic Scholar・arXiv


async def semantic_scholar_doi(http: httpx.AsyncClient, doi: str) -> list[BibResult]:
    r = await http.get(
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe='/')}",
        params={"fields": "title,abstract,year,venue,authors"},
    )
    if r.status_code == 404:
        return []
    r.raise_for_status()
    d = r.json()
    return [
        BibResult(
            found=True,
            doi=doi,
            title=clean(d.get("title"), 500),
            authors=[str(a.get("name")) for a in d.get("authors", []) if a.get("name")][:30],
            year=_year(d.get("year")),
            venue=clean(d.get("venue"), 300),
            source="semanticscholar",
            abstract=clean(d.get("abstract")),
        )
    ]


ATOM = "{http://www.w3.org/2005/Atom}"


async def arxiv_doi(http: httpx.AsyncClient, doi: str) -> list[BibResult]:
    match = re.fullmatch(r"10\.48550/arxiv\.(.+)", doi, re.IGNORECASE)
    if not match:
        return []
    r = await http.get("https://export.arxiv.org/api/query", params={"id_list": match.group(1)})
    r.raise_for_status()
    entry = ET.fromstring(r.text).find(f"{ATOM}entry")
    if entry is None:
        return []
    return [
        BibResult(
            found=True,
            doi=doi,
            title=clean(entry.findtext(f"{ATOM}title"), 500),
            authors=[
                clean(a.findtext(f"{ATOM}name"), 100) or "" for a in entry.findall(f"{ATOM}author")
            ][:30],
            year=_year(entry.findtext(f"{ATOM}published")),
            venue="arXiv",
            source="arxiv",
            abstract=clean(entry.findtext(f"{ATOM}summary")),
        )
    ]


# ---------------------------------------------------------------- CiNii Research・J-STAGE（日本語文献）


def parse_cinii(item: dict[str, Any]) -> BibResult:
    creators = item.get("dc:creator") or []
    if isinstance(creators, str):
        creators = [creators]
    identifiers = item.get("dc:identifier") or []
    if isinstance(identifiers, dict | str):
        identifiers = [identifiers]
    doi = None
    for ident in identifiers:
        value = ident.get("@value") if isinstance(ident, dict) else ident
        if value and str(value).startswith("10."):
            doi = str(value)
    return BibResult(
        found=True,
        doi=doi,
        title=clean(item.get("title"), 500),
        authors=[str(c) for c in creators][:30],
        year=_year(item.get("prism:publicationDate")),
        venue=clean(item.get("prism:publicationName"), 300),
        volume=clean(item.get("prism:volume"), 30),
        issue=clean(item.get("prism:number"), 30),
        pages=_pages(item.get("prism:startingPage"), item.get("prism:endingPage")),
        source="cinii",
        abstract=clean(item.get("description")),
    )


async def cinii_search(http: httpx.AsyncClient, title: str) -> list[BibResult]:
    r = await http.get(
        "https://cir.nii.ac.jp/opensearch/articles",
        params={"title": title, "format": "json", "count": "5"},
    )
    r.raise_for_status()
    return [parse_cinii(i) for i in r.json().get("items", [])]


PRISM = "{http://prismstandard.org/namespaces/basic/2.0/}"


def parse_jstage(entry: ET.Element) -> BibResult:
    def text(path: str) -> str | None:
        return clean(entry.findtext(path), 500)

    names = [n.text or "" for n in entry.findall(f"{ATOM}author/{ATOM}ja/{ATOM}name")] or [
        n.text or "" for n in entry.findall(f"{ATOM}author/{ATOM}en/{ATOM}name")
    ]
    return BibResult(
        found=True,
        doi=text(f"{PRISM}doi"),
        title=text(f"{ATOM}article_title/{ATOM}ja") or text(f"{ATOM}article_title/{ATOM}en"),
        authors=[n.strip() for n in names if n.strip()][:30],
        year=_year(entry.findtext(f"{ATOM}pubyear")),
        venue=text(f"{ATOM}material_title/{ATOM}ja") or text(f"{ATOM}material_title/{ATOM}en"),
        volume=text(f"{PRISM}volume"),
        issue=text(f"{PRISM}number"),
        pages=_pages(entry.findtext(f"{PRISM}startingPage"), entry.findtext(f"{PRISM}endingPage")),
        source="jstage",
    )


async def jstage_search(http: httpx.AsyncClient, title: str) -> list[BibResult]:
    r = await http.get(
        "https://api.jstage.jst.go.jp/searchapi/do",
        params={"service": "3", "article": title, "count": "5"},
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)
    return [parse_jstage(e) for e in root.findall(f"{ATOM}entry")]
