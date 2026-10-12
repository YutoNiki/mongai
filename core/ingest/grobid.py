"""GROBID（ローカルコンテナ）によるセクション構造・参考文献の抽出（SPEC 3.3-1）。

GROBID の consolidation（Crossref 等への問い合わせ）は常に無効にする（docs/dependencies.md）。
"""

import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from core.ingest.models import BBox, Reference

logger = logging.getLogger(__name__)

TEI = "{http://www.tei-c.org/ns/1.0}"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

DEFAULT_GROBID_URL = "http://grobid:8070"


@dataclass(frozen=True)
class GrobidHead:
    text: str
    number: str | None
    boxes: list[BBox]


@dataclass
class GrobidResult:
    title: str | None = None
    heads: list[GrobidHead] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)


class GrobidError(RuntimeError):
    pass


async def process_fulltext(
    pdf_path: str | Path,
    *,
    base_url: str | None = None,
    timeout: float = 300.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """GROBID の processFulltextDocument を呼び、TEI XML を返す。"""
    url = (base_url or os.environ.get("GROBID_URL") or DEFAULT_GROBID_URL).rstrip("/")
    data = {
        "consolidateHeader": "0",
        "consolidateCitations": "0",
        "consolidateFunders": "0",
        "includeRawCitations": "1",
        "teiCoordinates": ["head", "biblStruct"],
    }
    async with httpx.AsyncClient(timeout=timeout, transport=transport, trust_env=False) as http:
        with Path(pdf_path).open("rb") as f:
            try:
                response = await http.post(
                    f"{url}/api/processFulltextDocument", files={"input": f}, data=data
                )
            except httpx.HTTPError as exc:
                raise GrobidError(f"GROBID に接続できません：{type(exc).__name__}") from exc
    if response.status_code != 200:
        raise GrobidError(f"GROBID がエラーを返しました（HTTP {response.status_code}）")
    return response.text


def parse_coords(coords: str | None) -> list[BBox]:
    """GROBID の座標（"page,x,y,w,h;..."）を BBox に変換する。"""
    boxes: list[BBox] = []
    for part in (coords or "").split(";"):
        values = part.split(",")
        if len(values) != 5:
            continue
        try:
            page = int(values[0])
            x, y, w, h = (float(v) for v in values[1:])
        except ValueError:
            continue
        boxes.append(BBox(page=page, x0=x, y0=y, x1=x + w, y1=y + h))
    return boxes


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    text = " ".join("".join(el.itertext()).split())
    return text or None


def parse_tei(xml: str) -> GrobidResult:
    root = ET.fromstring(xml)
    result = GrobidResult()
    title = root.find(f".//{TEI}teiHeader/{TEI}fileDesc/{TEI}titleStmt/{TEI}title")
    result.title = _text(title)

    body = root.find(f".//{TEI}text/{TEI}body")
    if body is not None:
        for head in body.iter(f"{TEI}head"):
            text = _text(head)
            if text:
                result.heads.append(
                    GrobidHead(
                        text=text, number=head.get("n"), boxes=parse_coords(head.get("coords"))
                    )
                )

    back = root.find(f".//{TEI}text/{TEI}back")
    if back is not None:
        for i, bibl in enumerate(back.iter(f"{TEI}biblStruct")):
            result.references.append(_reference(bibl, i))
    return result


def _reference(bibl: ET.Element, index: int) -> Reference:
    analytic = bibl.find(f"{TEI}analytic")
    monogr = bibl.find(f"{TEI}monogr")
    title = _text(analytic.find(f"{TEI}title")) if analytic is not None else None
    venue = _text(monogr.find(f"{TEI}title")) if monogr is not None else None
    if title is None and venue is not None and monogr is not None:
        level = monogr.find(f"{TEI}title").get("level")  # type: ignore[union-attr]
        if level == "m":  # 単行本はタイトルとして扱う
            title, venue = venue, None

    authors: list[str] = []
    surnames: list[str] = []
    for pers in bibl.iter(f"{TEI}persName"):
        surname = _text(pers.find(f"{TEI}surname"))
        forenames = [t for t in (_text(f) for f in pers.findall(f"{TEI}forename")) if t]
        if not surname and not forenames:
            continue
        authors.append(" ".join([*forenames, surname or ""]).strip())
        if surname:
            surnames.append(surname)

    year: int | None = None
    date = bibl.find(f".//{TEI}imprint/{TEI}date")
    when = date.get("when") if date is not None else None
    if when and when[:4].isdigit():
        year = int(when[:4])

    def scope(unit: str) -> str | None:
        el = bibl.find(f".//{TEI}biblScope[@unit='{unit}']")
        if el is None:
            return None
        if el.get("from") and el.get("to"):
            return f"{el.get('from')}-{el.get('to')}"
        return _text(el) or el.get("from")

    doi = _text(bibl.find(f".//{TEI}idno[@type='DOI']"))
    raw = _text(bibl.find(f"{TEI}note[@type='raw_reference']"))
    return Reference(
        ref_id=f"R-{index + 1:03d}",
        authors=authors,
        surnames=surnames,
        title=title,
        venue=venue,
        year=year,
        volume=scope("volume"),
        issue=scope("issue"),
        pages=scope("page"),
        doi=doi,
        raw=raw,
        bboxes=parse_coords(bibl.get("coords")),
        source="grobid",
    )
