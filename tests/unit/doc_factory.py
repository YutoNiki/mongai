"""解析のテスト用に、PDF を使わずに合成の Document を作る。"""

from core.ingest.models import BBox, Document, Lang, Paragraph, Section, SectionRole, Sentence


def make_doc(
    sections: list[tuple[str, SectionRole, list[list[str]]]],
    *,
    lang: Lang = "ja",
    front: list[list[str]] | None = None,
) -> Document:
    """sections：(見出し, 役割, 段落ごとの文のリスト) のリスト。front は最初の見出しより前の段落。"""
    doc_sections: list[Section] = []
    paragraphs: list[Paragraph] = []
    sentences: list[Sentence] = []
    y = 100.0

    def add_paragraph(section: Section, texts: list[str]) -> None:
        nonlocal y
        pid = f"P-{len(paragraphs) + 1:04d}"
        ids: list[str] = []
        boxes: list[BBox] = []
        for text in texts:
            sid = f"S-{len(sentences) + 1:05d}"
            box = BBox(page=1 + len(sentences) // 20, x0=72, y0=y, x1=520, y1=y + 10)
            y = 100.0 if y > 700 else y + 14
            sentences.append(
                Sentence(
                    sentence_id=sid,
                    paragraph_id=pid,
                    section_id=section.section_id,
                    lang=lang,
                    text=text,
                    page=box.page,
                    bboxes=[box],
                )
            )
            ids.append(sid)
            boxes.append(box)
        paragraphs.append(
            Paragraph(
                paragraph_id=pid,
                section_id=section.section_id,
                kind="body",
                lang=lang,
                text="".join(texts) if lang == "ja" else " ".join(texts),
                page=boxes[0].page,
                bboxes=boxes,
                sentence_ids=ids,
            )
        )
        section.paragraph_ids.append(pid)

    if front:
        sec = Section(section_id="SEC-00", title="（冒頭）", role="abstract", page=1)
        doc_sections.append(sec)
        for texts in front:
            add_paragraph(sec, texts)
    for i, (title, role, paras) in enumerate(sections, start=1):
        number, _, name = title.partition(" ") if title[0].isdigit() else (None, "", title)
        sec = Section(
            section_id=f"SEC-{i:02d}", title=name, number=number or None, role=role, page=1
        )
        doc_sections.append(sec)
        for texts in paras:
            add_paragraph(sec, texts)
    return Document(
        doc_id="doc-test",
        title="合成論文",
        lang=lang,
        page_count=max(s.page for s in sentences),
        sections=doc_sections,
        paragraphs=paragraphs,
        sentences=sentences,
    )
