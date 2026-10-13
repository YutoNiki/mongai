"""二重チェック（SPEC 3.3-4）。重大・要修正の論理・読みやすさの指摘を、第2のモデルに独立に判定させる。

第1のモデルの判断に引きずられないよう、第2のモデルには指摘の種類と該当箇所だけを渡し、
指摘の説明・理由・改善案は渡さない。一致すれば confidence: high、不一致なら low とする。
引用の指摘（書誌の照合・アブストラクトとの照合）は外部のデータが根拠なので対象にしない。
"""

from typing import Literal

from core.analyze.context import numbered_paragraph
from core.analyze.models import Finding, ModelInfo, _LLMOutput
from core.ingest.models import Document, Lang, Paragraph
from core.llm.client import OitomaClient
from core.llm.prompts import load_prompt

ROLE = "second_opinion"
# 二重チェックの対象（SPEC 3.3-4）
TARGET_CATEGORIES = frozenset({"logic", "readability"})
TARGET_SEVERITIES = frozenset({"critical", "major"})


class SecondOpinion(_LLMOutput):
    valid: bool
    reason: str


def needs_second_opinion(finding: Finding) -> bool:
    return finding.category in TARGET_CATEGORIES and finding.severity in TARGET_SEVERITIES


def _paragraph_of(doc: Document, finding: Finding) -> tuple[Paragraph | None, Paragraph | None]:
    """指摘の文を含む段落と、その直前の本文の段落。"""
    owner = {sid: p for p in doc.paragraphs for sid in p.sentence_ids}
    paragraph = next((owner[s] for s in finding.sentence_ids if s in owner), None)
    if paragraph is None:
        return None, None
    body = [p for p in doc.paragraphs if p.kind == "body"]
    index = next((i for i, p in enumerate(body) if p.paragraph_id == paragraph.paragraph_id), None)
    previous = body[index - 1] if index else None
    return paragraph, previous


def _text(doc: Document, paragraph: Paragraph | None) -> str:
    if paragraph is None:
        return "-"
    sentences = {s.sentence_id: s for s in doc.sentences}
    return numbered_paragraph([sentences[s] for s in paragraph.sentence_ids if s in sentences])


async def second_opinion(
    client: OitomaClient,
    doc: Document,
    finding: Finding,
    *,
    prompt_lang: Lang,
    output_language: str,
) -> SecondOpinion:
    paragraph, previous = _paragraph_of(doc, finding)
    section = next((s for s in doc.sections if s.section_id == finding.section_id), None)
    section_label = (
        f"{section.number + ' ' if section.number else ''}{section.title}（{section.role}）"
        if section
        else "-"
    )
    messages = [
        {
            "role": "system",
            "content": load_prompt("second_opinion", prompt_lang, output_language=output_language),
        },
        {
            "role": "user",
            "content": load_prompt(
                "second_opinion_input",
                prompt_lang,
                section=section_label,
                context=_text(doc, previous),
                paragraph=_text(doc, paragraph),
                type=finding.subcategory,
                sentence_ids=", ".join(finding.sentence_ids),
                quote=finding.quote,
            ),
        },
    ]
    return await client.chat_json(ROLE, messages, SecondOpinion, lang=prompt_lang)


def apply_opinion(finding: Finding, opinion: SecondOpinion, model: str) -> Finding:
    """第2のモデルの判定を指摘に反映する（一致なら確度・高、不一致なら低）。"""
    confidence: Literal["high", "low"] = "high" if opinion.valid else "low"
    return finding.model_copy(
        update={
            "confidence": confidence,
            "models": ModelInfo(
                primary=finding.models.primary, secondary=model, agreed=opinion.valid
            ),
        }
    )
