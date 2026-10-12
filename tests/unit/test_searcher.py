"""searcher：書誌APIの応答の変換と、要求の振り分け（HTTP は respx で模擬し、外部には出ない）。"""

import httpx
import respx
from fastapi.testclient import TestClient

from searcher import apis
from searcher.main import app

JSTAGE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
 <entry>
  <article_title><en><![CDATA[ExtraPhrase]]></en><ja><![CDATA[生成型要約のための効率的データ拡張]]></ja></article_title>
  <author><ja><name><![CDATA[高瀬 翔]]></name></ja></author>
  <material_title><ja><![CDATA[自然言語処理]]></ja></material_title>
  <prism:volume>30</prism:volume><prism:number>2</prism:number>
  <prism:startingPage>489</prism:startingPage><prism:endingPage>506</prism:endingPage>
  <pubyear>2023</pubyear><prism:doi>10.5715/jnlp.30.489</prism:doi>
 </entry>
</feed>"""


def test_parse_crossref() -> None:
    r = apis.parse_crossref(
        {
            "DOI": "10.18653/v1/w19-5206",
            "title": ["Tagged <i>Back</i>-Translation"],
            "author": [{"given": "Isaac", "family": "Caswell"}],
            "issued": {"date-parts": [[2019, 8]]},
            "container-title": ["WMT"],
            "page": "53-63",
        }
    )
    assert (r.doi, r.title, r.authors, r.year, r.pages, r.source) == (
        "10.18653/v1/w19-5206",
        "Tagged Back -Translation",
        ["Isaac Caswell"],
        2019,
        "53-63",
        "crossref",
    )


def test_parse_openalex_rebuilds_abstract() -> None:
    r = apis.parse_openalex(
        {
            "doi": "https://doi.org/10.1/x",
            "title": "T",
            "publication_year": 2020,
            "authorships": [{"author": {"display_name": "A B"}}],
            "biblio": {"volume": "No.", "first_page": "1", "last_page": "9"},
            "abstract_inverted_index": {"world": [1], "hello": [0]},
        }
    )
    assert (r.doi, r.abstract, r.volume, r.pages) == ("10.1/x", "hello world", None, "1-9")


def test_parse_cinii_and_jstage() -> None:
    c = apis.parse_cinii(
        {
            "title": "生成型要約",
            "dc:creator": ["秋山 和輝"],
            "prism:publicationName": "自然言語処理",
            "prism:volume": "29",
            "prism:startingPage": "835",
            "prism:endingPage": "853",
            "prism:publicationDate": "2022",
            "description": "<p>本稿では</p>",
            "dc:identifier": [{"@type": "cir:DOI", "@value": "10.5715/jnlp.29.835"}],
        }
    )
    assert (c.doi, c.year, c.pages, c.abstract) == (
        "10.5715/jnlp.29.835",
        2022,
        "835-853",
        "本稿では",
    )
    import xml.etree.ElementTree as ET

    entry = ET.fromstring(JSTAGE).find(f"{apis.ATOM}entry")
    assert entry is not None
    j = apis.parse_jstage(entry)
    assert (j.title, j.authors, j.year, j.volume, j.issue, j.pages, j.doi) == (
        "生成型要約のための効率的データ拡張",
        ["高瀬 翔"],
        2023,
        "30",
        "2",
        "489-506",
        "10.5715/jnlp.30.489",
    )


@respx.mock
def test_bib_lookup_japanese_title_puts_japanese_sources_first() -> None:
    respx.get("https://api.jstage.jst.go.jp/searchapi/do").mock(
        return_value=httpx.Response(200, text=JSTAGE)
    )
    respx.get("https://cir.nii.ac.jp/opensearch/articles").mock(return_value=httpx.Response(500))
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(
            200, json={"message": {"items": [{"DOI": "10.1/noise", "title": ["Noise"]}]}}
        )
    )
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    r = TestClient(app).post(
        "/search", json={"kind": "bib_lookup", "title": "生成型要約のための効率的データ拡張"}
    )
    body = r.json()
    assert [x["source"] for x in body["results"]] == ["jstage", "crossref"]
    assert body["errors"] == ["cinii: HTTPStatusError"]


@respx.mock
def test_oa_fetch_falls_back_until_abstract_found() -> None:
    respx.get(url__regex=r"https://api\.openalex\.org/works/doi:.*").mock(
        return_value=httpx.Response(404)
    )
    respx.get(url__regex=r"https://api\.semanticscholar\.org/.*").mock(
        return_value=httpx.Response(200, json={"title": "T", "abstract": "An abstract."})
    )
    body = TestClient(app).post("/search", json={"kind": "oa_fetch", "doi": "10.1000/x"}).json()
    assert [(x["source"], x["abstract"]) for x in body["results"]] == [
        ("semanticscholar", "An abstract.")
    ]


def test_searcher_rejects_undefined_fields() -> None:
    r = TestClient(app).post(
        "/search", json={"kind": "oa_fetch", "doi": "10.1000/x", "query": "原稿の文"}
    )
    assert r.status_code == 422
