"""解析の対象の選び方と、LLM に渡すテキストの組み立て。"""

import json
import re
from dataclasses import dataclass
from typing import Literal

from core.analyze.models import ArgumentStructure
from core.ingest.models import Document, Lang, Paragraph, Section, SectionRole, Sentence

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
    previous: Paragraph | None  # 同じ文書で直前の解析対象の段落（文脈として渡す）

    @property
    def section_label(self) -> str:
        if self.section is None:
            return "-"
        number = f"{self.section.number} " if self.section.number else ""
        return f"{number}{self.section.title}（{self.section.role}）"


def select_targets(
    doc: Document, *, min_chars: int = 40, max_paragraphs: int | None = None
) -> list[Target]:
    sections = {s.section_id: s for s in doc.sections}
    sentences: dict[str, Sentence] = {s.sentence_id: s for s in doc.sentences}
    targets: list[Target] = []
    previous: Paragraph | None = None
    for para in doc.paragraphs:
        if para.kind != "body" or not para.sentence_ids:
            continue
        section = sections.get(para.section_id or "")
        if section is not None and section.role in SKIPPED_ROLES:
            continue
        if len(para.text) < min_chars or not _TERMINAL.search(para.text):
            continue
        in_front = section is not None and section.section_id == "SEC-00" and section.number is None
        if in_front and len(para.text) < _FRONT_MIN_CHARS[para.lang]:
            continue
        targets.append(
            Target(
                paragraph=para,
                sentences=[sentences[sid] for sid in para.sentence_ids if sid in sentences],
                section=section,
                previous=previous,
            )
        )
        previous = para
        if max_paragraphs is not None and len(targets) >= max_paragraphs:
            break
    return targets


def numbered_paragraph(sentences: list[Sentence]) -> str:
    return "\n".join(f"[{s.sentence_id}] {s.text}" for s in sentences)


def structure_text(structure: ArgumentStructure | None) -> str:
    if structure is None:
        return "-"
    data = structure.model_dump(exclude={"summary"})
    return json.dumps(data, ensure_ascii=False)


_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _SPACES.sub("", text)
