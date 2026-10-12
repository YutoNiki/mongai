"""PDF構造化の結果のデータモデル（SPEC 3.3-1）。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Lang = Literal["ja", "en"]

ParagraphKind = Literal["body", "heading", "caption", "footnote", "reference", "fragment"]

# セクションの役割（ルールの target_section と対応させる）
SectionRole = Literal[
    "title",
    "abstract",
    "introduction",
    "related_work",
    "methods",
    "experiments",
    "results",
    "discussion",
    "conclusion",
    "acknowledgements",
    "references",
    "appendix",
    "biography",
    "other",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BBox(_Model):
    """PDF上の矩形。page は1始まり、座標は PyMuPDF と同じ（左上原点、単位はポイント）。"""

    page: int = Field(ge=1)
    x0: float
    y0: float
    x1: float
    y1: float

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


class Sentence(_Model):
    sentence_id: str
    paragraph_id: str
    section_id: str | None = None
    lang: Lang
    text: str
    page: int = Field(ge=1)
    bboxes: list[BBox]


class Paragraph(_Model):
    paragraph_id: str
    section_id: str | None = None
    kind: ParagraphKind
    lang: Lang
    text: str
    page: int = Field(ge=1)
    bboxes: list[BBox]
    sentence_ids: list[str] = Field(default_factory=list)


class Section(_Model):
    section_id: str
    title: str
    number: str | None = None
    role: SectionRole
    page: int = Field(ge=1)
    paragraph_ids: list[str] = Field(default_factory=list)


class Reference(_Model):
    ref_id: str
    label: str | None = None  # 番号形式の引用で使うラベル（"12" など）
    authors: list[str] = Field(default_factory=list)  # 表示用の氏名（姓を先頭にしない）
    surnames: list[str] = Field(default_factory=list)  # 照合用の姓
    title: str | None = None
    venue: str | None = None
    year: int | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    raw: str | None = None
    # 参考文献リスト上の位置（GROBID の座標。分からない場合は空）
    bboxes: list[BBox] = Field(default_factory=list)
    # grobid：GROBID / llm：高速モデルで構造化 / layout：参考文献節の本文から補った（著者・年・原文のみ）
    source: Literal["grobid", "llm", "layout"] = "grobid"


class Citation(_Model):
    citation_id: str
    sentence_id: str
    marker: str  # 原稿上の表記（"[3-5]"、"Smith et al. (2020)" など）
    style: Literal["numeric", "author_year"]
    ref_ids: list[str] = Field(default_factory=list)
    resolved: bool  # 全ての要素が参考文献に対応付いたか


class Document(_Model):
    doc_id: str
    title: str | None = None
    lang: Lang
    page_count: int
    sections: list[Section] = Field(default_factory=list)
    paragraphs: list[Paragraph] = Field(default_factory=list)
    sentences: list[Sentence] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
