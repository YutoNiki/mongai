"""評価データセット（SPEC 5.4）の読み込みと、解析の入力（Target）への変換。

データセットは eval/datasets/*.jsonl に置く。すべて合成データ（実在の論文・未発表の原稿を含めない）。

- paragraph.jsonl：段落内チェック（前提と根拠の不一致・飛躍・過大な主張・問題なしの対照群）
- flow.jsonl：段落間チェック（接続の断絶・問題なしの対照群）
- citation_support.jsonl：引用内容の支持判定（引用の不一致・支持している対照群）

ルール違反（SPEC 5.4）は M6 のルールの判定を実装してから加える。
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.analyze.context import Target
from core.ingest.models import Lang, Paragraph, Section, SectionRole, Sentence

DATASET_DIR = Path(__file__).resolve().parent / "datasets"

ParagraphLabel = Literal["premise_evidence_mismatch", "leap", "overclaim", "none"]
FlowLabel = Literal["disconnection", "none"]
CitationLabel = Literal["does_not_support", "supports"]


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    lang: Lang
    note: str = ""  # 仕込んだ問題の説明（人が読むためのもの。モデルには渡さない）


class ParagraphItem(_Item):
    label: ParagraphLabel
    section_title: str
    section_role: SectionRole
    sentences: list[str] = Field(min_length=2)
    # 問題を仕込んだ文の番号（1始まり）。対照群は空
    target: list[int] = Field(default_factory=list)


class FlowItem(_Item):
    label: FlowLabel
    section_title: str
    section_role: SectionRole
    # 段落ごとの文のリスト（すべて同じセクション）
    paragraphs: list[list[str]] = Field(min_length=2)
    # 前の段落とのつながりが切れている段落の番号（1始まり）。対照群は None
    target: int | None = None


class CitationItem(_Item):
    label: CitationLabel
    sentence: str
    context: str
    reference: str
    abstract: str


def load_jsonl[T: BaseModel](path: Path, model: type[T]) -> list[T]:
    items: list[T] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            items.append(model.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"{path.name} の {number} 行目が不正です：{exc}") from exc
    ids = [i.id for i in items if isinstance(i, _Item)]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path.name} に重複した id があります")
    return items


def sentence_id(item_id: str, para: int, sent: int) -> str:
    return f"{item_id}-P{para}-S{sent}"


def paragraph_id(item_id: str, para: int) -> str:
    return f"{item_id}-P{para}"


def build_targets(
    item_id: str,
    lang: Lang,
    section_title: str,
    section_role: SectionRole,
    paragraphs: list[list[str]],
) -> list[Target]:
    """合成の段落を、実際の論文と同じ形（Target）に組み立てる。座標はダミー。"""
    section = Section(
        section_id=f"{item_id}-SEC",
        title=section_title,
        number="1",
        role=section_role,
        page=1,
        paragraph_ids=[paragraph_id(item_id, p) for p in range(1, len(paragraphs) + 1)],
    )
    paras: list[tuple[Paragraph, list[Sentence]]] = []
    for p, texts in enumerate(paragraphs, start=1):
        sentences = [
            Sentence(
                sentence_id=sentence_id(item_id, p, s),
                paragraph_id=paragraph_id(item_id, p),
                section_id=section.section_id,
                lang=lang,
                text=text,
                page=1,
                bboxes=[],
            )
            for s, text in enumerate(texts, start=1)
        ]
        joiner = "" if lang == "ja" else " "
        paragraph = Paragraph(
            paragraph_id=paragraph_id(item_id, p),
            section_id=section.section_id,
            kind="body",
            lang=lang,
            text=joiner.join(texts),
            page=1,
            bboxes=[],
            sentence_ids=[s.sentence_id for s in sentences],
        )
        paras.append((paragraph, sentences))
    targets: list[Target] = []
    for i, (paragraph, sentences) in enumerate(paras):
        previous = paras[i - 1][0] if i > 0 else None
        following = paras[i + 1][0] if i + 1 < len(paras) else None
        targets.append(Target(paragraph, sentences, section, previous, following))
    return targets
