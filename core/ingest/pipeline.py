"""PDF構造化のパイプライン：PyMuPDF（文と座標）＋GROBID（見出し・参考文献）＋引用の対応付け。"""

import hashlib
import logging
import re
from pathlib import Path

from core.ingest.citations import CitationResolver
from core.ingest.grobid import GrobidError, GrobidResult, parse_tei, process_fulltext
from core.ingest.layout import (
    Layout,
    RawParagraph,
    analyze_layout,
    char_boxes,
    split_hanging_entries,
)
from core.ingest.models import (
    BBox,
    Citation,
    Document,
    Lang,
    Paragraph,
    Reference,
    Section,
    SectionRole,
    Sentence,
)
from core.ingest.references import (
    clean_references,
    extract_with_llm,
    needs_fallback,
    supplement_from_text,
)
from core.ingest.sections import resolve_role, split_number
from core.ingest.sentences import detect_lang, split_sentences
from core.llm.client import OitomaClient, OitomaError

logger = logging.getLogger(__name__)

_LABEL = re.compile(r"^\s*\[?(\d{1,3})[\].]\s")
# 冒頭（最初の見出しより前）に要旨があるとみなす本文の長さ
_ABSTRACT_MIN_CHARS: dict[Lang, int] = {"ja": 150, "en": 400}


async def ingest_pdf(
    path: str | Path,
    *,
    doc_id: str | None = None,
    use_grobid: bool = True,
    grobid_url: str | None = None,
    llm: OitomaClient | None = None,
) -> Document:
    """PDF を構造化する。llm を渡すと、GROBID が参考文献を取れなかった場合に高速モデルで補う。"""
    path = Path(path)
    doc_id = doc_id or hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    layout = analyze_layout(path)
    warnings: list[str] = []

    grobid = GrobidResult()
    if use_grobid:
        try:
            grobid = parse_tei(await process_fulltext(path, base_url=grobid_url))
        except GrobidError as exc:
            warnings.append(f"GROBID を使えませんでした：{exc}")

    body_text = " ".join(p.text for p in layout.paragraphs if p.kind == "body")
    lang = detect_lang(body_text)

    _apply_grobid_heads(layout, grobid, warnings)
    paragraphs, sections = _build_sections(layout, lang)

    reference_text = split_hanging_entries(
        [raw for raw, p in zip(layout.paragraphs, paragraphs, strict=True) if p.kind == "reference"]
    )
    references = await _references(grobid, reference_text, lang, llm, warnings)

    resolver = CitationResolver(references)
    sentences: list[Sentence] = []
    citations: list[Citation] = []
    for raw, para in zip(layout.paragraphs, paragraphs, strict=True):
        if para.kind not in ("body", "caption"):
            continue
        for start, end in split_sentences(raw.text, para.lang):
            sid = f"S-{len(sentences) + 1:05d}"
            boxes = char_boxes(raw.chars[start:end]) or para.bboxes
            sentence = Sentence(
                sentence_id=sid,
                paragraph_id=para.paragraph_id,
                section_id=para.section_id,
                lang=para.lang,
                text=raw.text[start:end],
                page=boxes[0].page,
                bboxes=boxes,
            )
            sentences.append(sentence)
            para.sentence_ids.append(sid)
            if para.kind == "body":
                for match in resolver.find(sentence.text):
                    citations.append(
                        Citation(
                            citation_id=f"C-{len(citations) + 1:04d}",
                            sentence_id=sid,
                            marker=match.marker,
                            style="numeric" if match.style == "numeric" else "author_year",
                            ref_ids=match.ref_ids,
                            resolved=match.resolved,
                        )
                    )

    unresolved = sum(not c.resolved for c in citations)
    if unresolved:
        warnings.append(f"参考文献に対応付けられなかった引用が {unresolved} 件あります")

    return Document(
        doc_id=doc_id,
        title=layout.title or grobid.title or _front_title(paragraphs),
        lang=lang,
        page_count=layout.page_count,
        sections=sections,
        paragraphs=paragraphs,
        sentences=sentences,
        references=references,
        citations=citations,
        warnings=warnings,
    )


def _overlaps(box: BBox, para: RawParagraph) -> bool:
    for ln in para.lines:
        if ln.page != box.page:
            continue
        x0, y0, x1, y1 = ln.bbox
        if min(x1, box.x1) > max(x0, box.x0) and min(y1, box.y1) > max(y0, box.y0):
            return True
    return False


def _apply_grobid_heads(layout: Layout, grobid: GrobidResult, warnings: list[str]) -> None:
    """レイアウトから見出しをほとんど検出できなかった場合だけ、GROBID の見出しの座標で補う。"""
    if sum(p.kind == "heading" for p in layout.paragraphs) >= 2 or not grobid.heads:
        return
    marked = 0
    for head in grobid.heads:
        for para in layout.paragraphs:
            if (
                para.kind != "heading"
                and len(para.lines) <= 2
                and any(_overlaps(b, para) for b in head.boxes)
            ):
                para.kind = "heading"
                marked += 1
                break
    if marked:
        warnings.append(f"見出しの一部（{marked} 件）を GROBID の結果で補いました")


def _build_sections(layout: Layout, doc_lang: Lang) -> tuple[list[Paragraph], list[Section]]:
    paragraphs: list[Paragraph] = []
    sections: list[Section] = []
    known: dict[str, SectionRole] = {}

    first_heading = next(
        (i for i, p in enumerate(layout.paragraphs) if p.kind == "heading"), len(layout.paragraphs)
    )
    front = layout.paragraphs[:first_heading]
    if front:
        lang = detect_lang(" ".join(p.text for p in front)) if front else doc_lang
        has_abstract = any(
            p.kind == "body" and len(p.text) >= _ABSTRACT_MIN_CHARS[lang] for p in front
        )
        sections.append(
            Section(
                section_id="SEC-00",
                title="（冒頭）",
                role="abstract" if has_abstract else "title",
                page=front[0].page,
            )
        )

    current = sections[0] if sections else None
    for i, raw in enumerate(layout.paragraphs):
        if raw.kind == "heading":
            number, title = split_number(raw.text)
            role = resolve_role(number, title, known)
            if number:
                known[number] = role
            current = Section(
                section_id=f"SEC-{len(sections):02d}",
                title=title,
                number=number,
                role=role,
                page=raw.page,
            )
            sections.append(current)
        kind = raw.kind
        if current is not None and current.role == "references" and kind != "heading":
            kind = "reference"
        pid = f"P-{i + 1:04d}"
        para = Paragraph(
            paragraph_id=pid,
            section_id=current.section_id if current else None,
            kind=kind,
            lang=detect_lang(raw.text),
            text=raw.text,
            page=raw.page,
            bboxes=raw.bboxes(),
        )
        paragraphs.append(para)
        if current is not None:
            current.paragraph_ids.append(pid)
    return paragraphs, sections


async def _references(
    grobid: GrobidResult,
    reference_text: list[str],
    lang: Lang,
    llm: OitomaClient | None,
    warnings: list[str],
) -> list[Reference]:
    refs = grobid.references
    if needs_fallback(refs) and reference_text:
        if llm is not None:
            try:
                extracted = await extract_with_llm(llm, reference_text, lang)
            except OitomaError as exc:
                warnings.append(f"参考文献の構造化（高速モデル）に失敗しました：{exc}")
            else:
                if extracted:
                    return supplement_from_text(extracted, reference_text)
                # 高速モデルが何も返さなかった場合は、本文から補う方法に切り替える
                warnings.append("参考文献の構造化（高速モデル）で文献を取り出せませんでした")
        else:
            warnings.append("GROBID で参考文献を十分に構造化できませんでした")
    cleaned = [_with_label(r) for r in clean_references(refs)]
    supplemented = supplement_from_text(cleaned, reference_text)
    if len(supplemented) > len(cleaned):
        warnings.append(
            f"GROBID が取りこぼした参考文献 {len(supplemented) - len(cleaned)} 件を本文から補いました"
        )
    return supplemented


def _with_label(ref: Reference) -> Reference:
    if ref.label is None and ref.raw:
        m = _LABEL.match(ref.raw)
        if m:
            return ref.model_copy(update={"label": m.group(1)})
    return ref


def _front_title(paragraphs: list[Paragraph]) -> str | None:
    for p in paragraphs:
        if p.kind in ("body", "heading") and 5 <= len(p.text) <= 200:
            return p.text
    return None
