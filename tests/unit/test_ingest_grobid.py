"""GROBID の呼び出し（consolidation の無効化）と TEI の解析。"""

from pathlib import Path

import httpx
import pytest
import respx

from core.ingest.grobid import GrobidError, parse_coords, parse_tei, process_fulltext

TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
 <teiHeader><fileDesc><titleStmt><title level="a" type="main">A Synthetic Paper</title></titleStmt>
 </fileDesc></teiHeader>
 <text><body>
  <div><head n="1" coords="1,70.0,500.0,80.0,12.0">Introduction</head><p>Text.</p></div>
  <div><head n="2">Method</head></div>
 </body>
 <back><div type="references"><listBibl>
  <biblStruct xml:id="b0" coords="7,70,90,220,12">
   <analytic><title level="a" type="main">Neural machine translation</title>
    <author><persName><forename type="first">Dzmitry</forename><surname>Bahdanau</surname></persName></author>
    <author><persName><forename type="first">Kyunghyun</forename><surname>Cho</surname></persName></author>
   </analytic>
   <monogr><title level="m">ICLR</title>
    <imprint><date type="published" when="2015" /><biblScope unit="page" from="1" to="15" /></imprint>
   </monogr>
   <idno type="DOI">10.1000/xyz</idno>
   <note type="raw_reference">[1] Bahdanau, D. and Cho, K. 2015. Neural machine translation.</note>
  </biblStruct>
  <biblStruct xml:id="b1">
   <monogr><title level="j">Journal of Tests</title>
    <author><persName><surname>Lee</surname></persName></author>
    <imprint><date when="2019-05"/><biblScope unit="volume">3</biblScope></imprint>
   </monogr>
  </biblStruct>
 </listBibl></div></back>
 </text>
</TEI>
"""


def test_parse_coords() -> None:
    boxes = parse_coords("2,10,20,30,40;3,1.5,2.5,1,1;bad")
    assert [(b.page, b.x0, b.y0, b.x1, b.y1) for b in boxes] == [
        (2, 10.0, 20.0, 40.0, 60.0),
        (3, 1.5, 2.5, 2.5, 3.5),
    ]
    assert parse_coords(None) == []


def test_parse_tei() -> None:
    result = parse_tei(TEI)
    assert result.title == "A Synthetic Paper"
    assert [(h.number, h.text) for h in result.heads] == [("1", "Introduction"), ("2", "Method")]
    assert result.heads[0].boxes[0].page == 1
    first, second = result.references
    assert first.ref_id == "R-001"
    assert first.surnames == ["Bahdanau", "Cho"]
    assert first.authors == ["Dzmitry Bahdanau", "Kyunghyun Cho"]
    assert first.title == "Neural machine translation"
    assert first.year == 2015
    assert first.pages == "1-15"
    assert first.doi == "10.1000/xyz"
    assert first.raw is not None and first.raw.startswith("[1]")
    assert second.title is None
    assert second.venue == "Journal of Tests"
    assert second.year == 2019
    assert second.volume == "3"


@respx.mock
async def test_process_fulltext_disables_consolidation(tmp_path: Path) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic")
    route = respx.post("http://grobid:8070/api/processFulltextDocument").mock(
        return_value=httpx.Response(200, text=TEI)
    )
    assert await process_fulltext(pdf, base_url="http://grobid:8070") == TEI
    body = route.calls.last.request.content.decode("latin-1")
    for field in ("consolidateHeader", "consolidateCitations", "consolidateFunders"):
        assert f'name="{field}"\r\n\r\n0' in body


@respx.mock
async def test_process_fulltext_errors(tmp_path: Path) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    respx.post("http://grobid:8070/api/processFulltextDocument").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(GrobidError):
        await process_fulltext(pdf, base_url="http://grobid:8070")
