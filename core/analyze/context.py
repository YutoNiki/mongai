"""解析の対象の選び方と、LLM に渡すテキストの組み立て。"""

import json
import re
from dataclasses import dataclass
from typing import Literal

from core.analyze.models import ArgumentStructure
from core.ingest.models import Document, Lang, Paragraph, Section, SectionRole, Sentence
from core.instructions.models import DocumentType

OutputLang = Literal["paper", "ja"]

# 文末の記号。これを1つも含まない段落（著者名・キーワードの列挙など）は解析しない
_TERMINAL = re.compile(r"[。．.!?！？]")
# 最初の見出しより前（タイトル・著者・要旨）の段落は、要旨とみなせる長さのものだけ解析する
_FRONT_MIN_CHARS: dict[Lang, int] = {"ja": 150, "en": 400}

# 論理・読みやすさの解析の対象外にするセクション
SKIPPED_ROLES: frozenset[SectionRole] = frozenset(
    {"title", "references", "acknowledgements", "biography"}
)

_OUTPUT_LANGUAGE: dict[tuple[Lang, Lang], str] = {
    # (プロンプトの言語, 指摘を書く言語) → プロンプトに入れる言語名
    ("ja", "ja"): "日本語",
    ("ja", "en"): "英語",
    ("en", "en"): "English",
    ("en", "ja"): "Japanese",
}


def output_language(prompt_lang: Lang, paper_lang: Lang, setting: OutputLang) -> str:
    target: Lang = paper_lang if setting == "paper" else "ja"
    return _OUTPUT_LANGUAGE[(prompt_lang, target)]


@dataclass(frozen=True)
class Target:
    """解析の対象の段落と、その周辺の情報。"""

    paragraph: Paragraph
    sentences: list[Sentence]
    section: Section | None
    previous: Paragraph | None  # 短い段落も含む、直前の本文
    next: Paragraph | None = None

    @property
    def section_label(self) -> str:
        if self.section is None:
            return "-"
        number = f"{self.section.number} " if self.section.number else ""
        return f"{number}{self.section.title}（{self.section.role}）"


def context_paragraphs(doc: Document) -> list[Paragraph]:
    roles = {s.section_id: s.role for s in doc.sections}
    return [
        p
        for p in doc.paragraphs
        if p.kind == "body" and roles.get(p.section_id or "") not in SKIPPED_ROLES
    ]


def select_targets(
    doc: Document,
    *,
    min_chars: int = 40,
    max_paragraphs: int | None = None,
    document_type: DocumentType = "paper",
) -> list[Target]:
    sections = {s.section_id: s for s in doc.sections}
    sentences: dict[str, Sentence] = {s.sentence_id: s for s in doc.sentences}
    targets: list[Target] = []
    body = context_paragraphs(doc)
    for index, para in enumerate(body):
        if para.kind != "body" or not para.sentence_ids:
            continue
        section = sections.get(para.section_id or "")
        if section is not None and section.role in SKIPPED_ROLES:
            continue
        threshold = min_chars if document_type == "paper" else 1
        if len(para.text) < threshold or not _TERMINAL.search(para.text):
            continue
        in_front = section is not None and section.section_id == "SEC-00" and section.number is None
        if document_type == "paper" and in_front and len(para.text) < _FRONT_MIN_CHARS[para.lang]:
            continue
        targets.append(
            Target(
                paragraph=para,
                sentences=[sentences[sid] for sid in para.sentence_ids if sid in sentences],
                section=section,
                previous=body[index - 1] if index else None,
                next=body[index + 1] if index + 1 < len(body) else None,
            )
        )
        if max_paragraphs is not None and len(targets) >= max_paragraphs:
            break
    return targets


def numbered_paragraph(sentences: list[Sentence]) -> str:
    """段落を、文IDを文頭に埋め込んだ1つの続いた文章として返す。

    1行1文の形にすると、モデルが文を切り離して読み、段落としてのつながりを見落としやすいため。
    """
    if not sentences:
        return "-"
    joiner = "" if sentences[0].lang == "ja" else " "
    return joiner.join(f"【{s.sentence_id}】{s.text}" for s in sentences)


def structure_text(structure: ArgumentStructure | None) -> str:
    if structure is None:
        return "-"
    data = structure.model_dump(exclude={"summary"})
    return json.dumps(data, ensure_ascii=False)


_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _SPACES.sub("", text)
