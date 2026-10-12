"""改善案の生成（SPEC 3.3-5）。元の文意と専門用語を変えない書き換え案を1〜2個作る。"""

import re

from core.analyze.context import normalize
from core.analyze.models import Finding, SuggestionOutput
from core.ingest.models import Document, Lang
from core.llm.client import OitomaClient
from core.llm.prompts import load_prompt

ROLE = "suggestion"

# 書き換え案に必ず残すべき語：引用の表記、数値、英大文字や数字を含む語（モデル名・略語など）
_PROTECTED = re.compile(
    r"\[[0-9,\s–-]+\]"  # [12]、[1, 3]
    r"|\([^()]*\d{4}[a-z]?\)"  # (Smith, 2020)
    r"|（[^（）]*\d{4}[a-z]?）"  # （田中, 2020）
    r"|\d+(?:\.\d+)?%?"  # 数値
    r"|[A-Za-z]*[A-Z0-9][A-Za-z0-9\-]*[A-Z0-9][A-Za-z0-9\-]*"  # BERT、ROUGE-1、GPT-4
)


def protected_terms(text: str) -> list[str]:
    return sorted({m.group(0) for m in _PROTECTED.finditer(text)})


def keeps_terms(original: str, rewrite: str) -> bool:
    return all(term in rewrite for term in protected_terms(original))


def _context(doc: Document, finding: Finding) -> str:
    order = [s.sentence_id for s in doc.sentences]
    texts = {s.sentence_id: s.text for s in doc.sentences}
    indexes = [order.index(sid) for sid in finding.sentence_ids if sid in texts]
    if not indexes:
        return "-"
    start, end = max(0, min(indexes) - 2), min(len(order), max(indexes) + 3)
    return "\n".join(texts[order[i]] for i in range(start, end))


async def suggest(
    client: OitomaClient, doc: Document, finding: Finding, *, prompt_lang: Lang
) -> list[str]:
    """書き換え案を返す。専門用語・数値・引用の表記が消えた案や、原文と同じ案は除く。"""
    messages = [
        {"role": "system", "content": load_prompt("suggestion", prompt_lang)},
        {
            "role": "user",
            "content": load_prompt(
                "suggestion_input",
                prompt_lang,
                type=finding.subcategory,
                issue=finding.issue,
                rationale=finding.rationale,
                quote=finding.quote,
                context=_context(doc, finding),
            ),
        },
    ]
    result = await client.chat_json(ROLE, messages, SuggestionOutput, lang=prompt_lang)
    # 原文（引用箇所、または指摘された文の全文）と同じ案は、書き換えになっていないので除く
    texts = {s.sentence_id: s.text for s in doc.sentences}
    originals = {normalize(finding.quote)} | {
        normalize(texts[sid]) for sid in finding.sentence_ids if sid in texts
    }
    accepted: list[str] = []
    for rewrite in result.suggestions:
        rewrite = rewrite.strip()
        if not rewrite or normalize(rewrite) in originals or rewrite in accepted:
            continue
        if keeps_terms(finding.quote, rewrite):
            accepted.append(rewrite)
    return accepted[:2]
