"""過去の指摘に類似する箇所の検出（SPEC 3.3-3 の「専門知識」）。

1. 段落（検索の指示を付けて）と過去の指摘を埋め込み（役割 retrieval）で比べ、近い指摘を段落ごとに
   最大 MAX_CANDIDATES 件選ぶ
2. 高速モデル（役割 lab_check）に、同じ種類の問題が段落に実際にあるかを判定させる
   （話題が似ているだけのものを除くため。埋め込みの近さだけでは指摘にしない）
3. あれば「以前、…と指摘されています」という指摘（カテゴリ「専門知識」）にする
"""

import math
from dataclasses import dataclass

from pydantic import Field

from core.analyze.context import Target, normalize, numbered_paragraph
from core.analyze.models import Severity, _LLMOutput
from core.ingest.models import BBox, Lang
from core.lab.models import LabComment
from core.llm.client import OitomaClient
from core.llm.prompts import load_prompt

ROLE = "lab_check"
EMBEDDING_ROLE = "retrieval"
# 1段落の判定に使う過去の指摘の上限と、候補にする埋め込みの類似度の下限。
# 段落と一般化した指摘の類似度は低めに出て、関係の有無をよく区別できない（2026-10-13 の試行で 0.2〜0.6）。
# そのため下限は低くし、上位の候補を高速モデルに渡して、同じ問題があるかを判定させる
MAX_CANDIDATES = 3
MIN_SIMILARITY = 0.25
# 段落を検索の問い合わせとして埋め込むときの指示（qwen3-embedding は指示付きの問い合わせを想定している）
QUERY_INSTRUCTION = (
    "Instruct: Given a paragraph of a research paper, retrieve past review comments that point out "
    "the same kind of problem in it\nQuery: "
)


class LabMatchItem(_LLMOutput):
    comment_id: str
    sentence_ids: list[str]
    quote: str
    issue: str
    severity: Severity = "suggestion"


class LabCheckOutput(_LLMOutput):
    matches: list[LabMatchItem] = Field(default_factory=list)


@dataclass
class LabMatch:
    """過去の指摘に類似する箇所（番号を振る前。pipeline で Finding にする）。"""

    comment: LabComment
    sentence_ids: list[str]
    bboxes: list[BBox]
    quote: str
    issue: str
    severity: Severity
    model: str


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def candidates_for(
    vector: list[float],
    comments: list[LabComment],
    vectors: dict[str, list[float]],
    *,
    limit: int = MAX_CANDIDATES,
    minimum: float = MIN_SIMILARITY,
) -> list[LabComment]:
    scored = [
        (_cosine(vector, vectors[c.comment_id]), c) for c in comments if c.comment_id in vectors
    ]
    scored = [x for x in scored if x[0] >= minimum]
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]


async def embed_targets(client: OitomaClient, targets: list[Target]) -> list[list[float]]:
    return await client.embed(
        [QUERY_INSTRUCTION + t.paragraph.text for t in targets], role=EMBEDDING_ROLE
    )


async def check_lab(
    client: OitomaClient,
    target: Target,
    candidates: list[LabComment],
    *,
    prompt_lang: Lang,
    output_language: str,
) -> list[LabMatch]:
    if not candidates:
        return []
    by_id = {c.comment_id: c for c in candidates}
    listed = "\n".join(
        f"[{c.comment_id}] {c.comment}"
        + (f"（指摘された箇所の例：{c.example}）" if c.example else "")
        for c in candidates
    )
    messages = [
        {
            "role": "system",
            "content": load_prompt(ROLE, prompt_lang, output_language=output_language),
        },
        {
            "role": "user",
            "content": load_prompt(
                "lab_check_input",
                prompt_lang,
                section=target.section_label,
                paragraph=numbered_paragraph(target.sentences),
                comments=listed,
            ),
        },
    ]
    output = await client.chat_json(ROLE, messages, LabCheckOutput, lang=prompt_lang)
    texts = {s.sentence_id: s for s in target.sentences}
    matches: list[LabMatch] = []
    seen: set[str] = set()
    for item in output.matches:
        comment = by_id.get(item.comment_id)
        ids = [sid for sid in item.sentence_ids if sid in texts]
        if comment is None or not ids or comment.comment_id in seen:
            continue
        seen.add(comment.comment_id)
        joined = normalize("".join(texts[sid].text for sid in ids))
        quote = item.quote.strip()
        if not quote or normalize(quote) not in joined:
            quote = texts[ids[0]].text
        matches.append(
            LabMatch(
                comment=comment,
                sentence_ids=ids,
                bboxes=[b for sid in ids for b in texts[sid].bboxes],
                quote=quote,
                issue=item.issue,
                severity=item.severity,
                model=client.config.role(ROLE).model,
            )
        )
    return matches
