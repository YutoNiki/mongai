"""論証構造の抽出（SPEC 3.3-2）。高速モデルで段落ごとに並列に実行する。"""

from core.analyze.context import Target, numbered_paragraph
from core.analyze.models import ArgumentStructure, ArgumentUnit, ParagraphStructure
from core.ingest.models import Lang
from core.llm.client import OitomaClient
from core.llm.prompts import load_prompt

ROLE = "structure_extraction"


async def extract_structure(
    client: OitomaClient, target: Target, *, prompt_lang: Lang, output_language: str
) -> ParagraphStructure:
    messages = [
        {
            "role": "system",
            "content": load_prompt(
                "argument_structure", prompt_lang, output_language=output_language
            ),
        },
        {
            "role": "user",
            "content": load_prompt(
                "argument_structure_input",
                prompt_lang,
                section=target.section_label,
                paragraph=numbered_paragraph(target.sentences),
            ),
        },
    ]
    result = await client.chat_json(ROLE, messages, ArgumentStructure, lang=prompt_lang)
    valid = {s.sentence_id for s in target.sentences}

    def keep(units: list[ArgumentUnit]) -> list[ArgumentUnit]:
        cleaned = []
        for unit in units:
            ids = [sid for sid in unit.sentence_ids if sid in valid]
            if ids:
                cleaned.append(unit.model_copy(update={"sentence_ids": ids}))
        return cleaned

    return ParagraphStructure(
        paragraph_id=target.paragraph.paragraph_id,
        claims=keep(result.claims),
        premises=keep(result.premises),
        evidence=keep(result.evidence),
        connectives=result.connectives,
        novelty_claim=result.novelty_claim,
        summary=result.summary,
    )


def fallback_structure(target: Target) -> ParagraphStructure:
    """抽出に失敗した段落の代わり（要約には先頭の文を使う）。"""
    first = target.sentences[0].text if target.sentences else target.paragraph.text[:200]
    return ParagraphStructure(paragraph_id=target.paragraph.paragraph_id, summary=first)
