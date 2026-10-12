"""段落内の論理・読みやすさのチェック（SPEC 3.3-3）。思考モデルで複数回判定し、多数決を取る。"""

import asyncio
import re

from core.analyze.context import Target, normalize, numbered_paragraph, structure_text
from core.analyze.models import ParagraphCheckOutput, ParagraphIssue, ParagraphStructure
from core.analyze.voting import Voted, majority
from core.ingest.models import Lang
from core.llm.client import JSONOutputError, OitomaClient
from core.llm.prompts import load_prompt

ROLE = "logic_check"


def samples_for(client: OitomaClient, role: str) -> int:
    cfg = client.config.role(role)
    return cfg.samples if cfg.vote == "majority" else 1


def same_issue(a: ParagraphIssue, b: ParagraphIssue) -> bool:
    return a.type == b.type and bool(set(a.sentence_ids) & set(b.sentence_ids))


# 文末の記号（閉じ括弧で終わる文も含む）。これで終わらない文は、抽出の都合で途切れている可能性がある
_COMPLETE = re.compile(r"[。．.!?！？][」』）)\]\"'”’]*$")


def is_complete(text: str) -> bool:
    return _COMPLETE.search(text.strip()) is not None


def clean_issues(
    issues: list[ParagraphIssue], target: Target, known_sentences: set[str]
) -> list[ParagraphIssue]:
    """出力を検証して整える。

    - 段落外の文IDを除き、原文に無い引用（quote）は該当する文の全文に置き換える
    - 文の途切れ・未完を理由にした指摘は捨てる（PDF から抽出した都合で起きるため。モデルの自己申告）
    - 文末の記号で終わらない文への「主語と述語のねじれ」は捨てる（同上の誤検出を機械的に防ぐ）
    """
    texts = {s.sentence_id: s.text for s in target.sentences}
    cleaned: list[ParagraphIssue] = []
    for issue in issues:
        ids = [sid for sid in issue.sentence_ids if sid in texts]
        if not ids or issue.about_truncation:
            continue
        if issue.type == "subject_predicate_mismatch" and not all(
            is_complete(texts[sid]) for sid in ids
        ):
            continue
        related = [sid for sid in issue.related_sentence_ids if sid in known_sentences]
        joined = normalize("".join(texts[sid] for sid in ids))
        quote = issue.quote.strip()
        if not quote or normalize(quote) not in joined:
            quote = texts[ids[0]]
        cleaned.append(
            issue.model_copy(
                update={"sentence_ids": ids, "related_sentence_ids": related, "quote": quote}
            )
        )
    return cleaned


async def check_paragraph(
    client: OitomaClient,
    target: Target,
    structure: ParagraphStructure | None,
    *,
    prompt_lang: Lang,
    output_language: str,
    known_sentences: set[str],
    context_text: str,
    next_text: str = "",
) -> tuple[list[Voted[ParagraphIssue]], int]:
    """段落を n 回判定し、過半数に現れた指摘と n を返す。"""
    messages = [
        {
            "role": "system",
            "content": load_prompt("paragraph_check", prompt_lang, output_language=output_language),
        },
        {
            "role": "user",
            "content": load_prompt(
                "paragraph_check_input",
                prompt_lang,
                section=target.section_label,
                context=context_text or "-",
                paragraph=numbered_paragraph(target.sentences),
                next=next_text or "-",
                structure=structure_text(structure),
            ),
        },
    ]
    n = samples_for(client, ROLE)
    outcomes = await asyncio.gather(
        *(
            client.chat_json(ROLE, messages, ParagraphCheckOutput, lang=prompt_lang)
            for _ in range(n)
        ),
        return_exceptions=True,
    )
    samples = [
        clean_issues(o.issues, target, known_sentences)
        for o in outcomes
        if isinstance(o, ParagraphCheckOutput)
    ]
    if not samples:
        error = next(o for o in outcomes if isinstance(o, BaseException))
        raise JSONOutputError(
            f"段落 {target.paragraph.paragraph_id} の判定に失敗しました"
        ) from error
    return majority(samples, same=same_issue, n=n), n
