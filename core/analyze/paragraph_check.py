"""段落内の論理・読みやすさのチェック（SPEC 3.3-3）。思考モデルで複数回判定し、多数決を取る。"""

import re
from collections.abc import Callable

from core.analyze.context import Target, normalize, numbered_paragraph, structure_text
from core.analyze.evidence import quote_matches
from core.analyze.models import ParagraphCheckOutput, ParagraphIssue, ParagraphStructure
from core.analyze.voting import Voted, vote
from core.ingest.models import Lang
from core.instructions.models import DocumentType
from core.instructions.review import review_prompt
from core.llm.client import JSONOutputError, OitomaClient
from core.llm.prompts import load_prompt

ROLE = "logic_check"


def samples_for(client: OitomaClient, role: str) -> int:
    cfg = client.config.role(role)
    return cfg.samples if cfg.vote == "majority" else 1


def early_stop_for(client: OitomaClient, role: str) -> bool:
    return client.config.role(role).early_stop


def same_issue(a: ParagraphIssue, b: ParagraphIssue) -> bool:
    qa, qb = normalize(a.quote), normalize(b.quote)
    return (
        a.type == b.type
        and bool(set(a.sentence_ids) & set(b.sentence_ids))
        and bool(qa and qb)
        and (qa in qb or qb in qa)
    )


# 文末の記号（閉じ括弧で終わる文も含む）。これで終わらない文は、抽出の都合で途切れている可能性がある
_COMPLETE = re.compile(r"[。．.!?！？][」』）)\]\"'”’]*$")


def is_complete(text: str) -> bool:
    return _COMPLETE.search(text.strip()) is not None


def clean_issues(
    issues: list[ParagraphIssue], target: Target, known_sentences: set[str]
) -> list[ParagraphIssue]:
    """出力を検証して整える。

    - 段落外の文IDを除き、原文に無い引用を含む指摘は確定しない
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
            continue
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
    document_type: DocumentType = "paper",
    on_warning: Callable[[str], None] | None = None,
) -> tuple[list[Voted[ParagraphIssue]], int]:
    """段落を最大 n 回判定し、過半数に現れた指摘と、実際に判定した回数を返す。"""
    messages = [
        {
            "role": "system",
            "content": review_prompt(
                "paragraph_check", prompt_lang, document_type, output_language=output_language
            ),
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

    def validate(output: ParagraphCheckOutput) -> ParagraphCheckOutput:
        texts = {s.sentence_id: s.text for s in target.sentences}
        for item in output.issues:
            if not item.about_truncation and not quote_matches(
                item.quote, (texts[sid] for sid in item.sentence_ids if sid in texts)
            ):
                raise ValueError("指摘の文IDと原文引用を確認してください")
        return output

    async def judge() -> list[ParagraphIssue]:
        output = await client.chat_json(
            ROLE, messages, ParagraphCheckOutput, lang=prompt_lang, validate=validate
        )
        return clean_issues(output.issues, target, known_sentences)

    ballot = await vote(
        judge, same=same_issue, n=samples_for(client, ROLE), early_stop=early_stop_for(client, ROLE)
    )
    if ballot.all_failed or (
        ballot.errors and ballot.attempts - len(ballot.errors) < samples_for(client, ROLE) // 2 + 1
    ):
        raise JSONOutputError(
            f"段落 {target.paragraph.paragraph_id} は有効な判定が不足しているため未確認です"
        ) from ballot.errors[0]
    if ballot.errors and on_warning:
        on_warning(
            f"段落 {target.paragraph.paragraph_id} の判定は一部失敗しました"
            f"（{len(ballot.errors)}/{ballot.attempts} 回）"
        )
    return ballot.voted, ballot.attempts
