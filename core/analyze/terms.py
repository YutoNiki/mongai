"""未知の用語の照会（SPEC 3.3-3 の「未知の用語」、M7）。

1. 高速モデル（役割 term_extraction）が、節ごとに「定義なしで使われていて、隣接分野の読者が調べたくなる
   一般的な専門用語」を選ぶ（著者の造語・既存の手法名・固有名詞・論文の主題の語は選ばない。
   タイトルに含まれる語は、コードでも除く）
2. 論文全体で最大 MAX_TERMS 語を、gateway に term_lookup（1語だけのスキーマ）として送る
3. standard モードでは term_lookup は承認制なので、承認キューに積まれる。利用者が承認するまで学外には出ない。
   gateway は、原稿で定義された造語や、新規性に関わる主張に近い語を拒否する

学内の検索（direction・addi(c)tion・LOCAL RAG）は API の仕様が不明のため、まだ使っていない（TODO M7）。
照会の記録と結果（学外の辞書の説明）は解析ごとに保存し、ダッシュボードに表示する。指摘にはまだ使わない。
"""

import asyncio
from dataclasses import dataclass, field

from pydantic import Field, ValidationError

from core.analyze.context import Target, numbered_paragraph
from core.analyze.models import _LLMOutput
from core.gateway_client import GatewayClient, GatewayError
from core.ingest.models import Document, Lang
from core.llm.client import OitomaClient, OitomaError
from core.llm.prompts import load_prompt
from core.rules.check import group_targets
from core.store import TermLookupRecord
from core.store.db import TermStatus
from shared.schemas import TermLookup

ROLE = "term_extraction"
# 1本の論文から照会する用語の上限（承認キューを埋めすぎないため）
MAX_TERMS = 5


class TermOutput(_LLMOutput):
    terms: list[str] = Field(default_factory=list)


@dataclass
class TermLookupResult:
    pending: list[str] = field(default_factory=list)  # 承認待ちに積んだ用語
    resolved: list[str] = field(default_factory=list)  # キャッシュ・送信済みで結果が得られた用語
    blocked: int = 0  # gateway が拒否した数
    records: list[TermLookupRecord] = field(default_factory=list)  # 解析ごとに保存する照会の記録
    warnings: list[str] = field(default_factory=list)


def pick_terms(
    candidates: list[str],
    lang: Lang,
    *,
    title: str | None = None,
    cited_sentences: list[str] | None = None,
    limit: int = MAX_TERMS,
) -> list[str]:
    """重複を除き、term_lookup のスキーマに合うものだけを最大 limit 語選ぶ（順序は保つ）。

    - 論文のタイトルに含まれる語は選ばない（未発表の論文の主題を、用語の照会で学外に出さないため）
    - 引用の付いた文に出てくる語は選ばない（既存の手法・モデルの名前で、引用文献が説明している）
    """
    chosen: list[str] = []
    seen: set[str] = set()
    own_title = (title or "").casefold()
    cited = [s.casefold() for s in cited_sentences or []]
    for raw in candidates:
        term = raw.strip()
        key = term.casefold()
        if not term or key in seen or (own_title and key in own_title):
            continue
        if any(key in s for s in cited):
            continue
        try:
            TermLookup(kind="term_lookup", term=term, lang=lang)
        except ValidationError:
            continue
        seen.add(key)
        chosen.append(term)
        if len(chosen) >= limit:
            break
    return chosen


async def extract_terms(
    client: OitomaClient, group: list[Target], *, prompt_lang: Lang
) -> list[str]:
    paragraphs = "\n\n".join(numbered_paragraph(t.sentences) for t in group)
    messages = [
        {"role": "system", "content": load_prompt(ROLE, prompt_lang)},
        {
            "role": "user",
            "content": load_prompt(
                "term_extraction_input",
                prompt_lang,
                section=group[0].section_label,
                paragraphs=paragraphs,
            ),
        },
    ]
    output = await client.chat_json(ROLE, messages, TermOutput, lang=prompt_lang)
    return output.terms


async def lookup_terms(
    client: OitomaClient,
    gateway: GatewayClient,
    doc: Document,
    targets: list[Target],
    *,
    analysis_id: str,
    lang: Lang,
) -> TermLookupResult:
    """用語を選んで gateway に照会する。gateway への原稿の登録（引用の検証）の後に呼ぶ。"""
    result = TermLookupResult()
    groups = group_targets(targets)
    outputs = await asyncio.gather(
        *(extract_terms(client, g, prompt_lang=lang) for g in groups), return_exceptions=True
    )
    candidates: list[str] = []
    for output in outputs:
        if isinstance(output, OitomaError):
            result.warnings.append(f"用語の抽出に失敗しました：{output}")
        elif isinstance(output, BaseException):
            raise output
        else:
            candidates.extend(output)
    citing = {c.sentence_id for c in doc.citations}
    cited_sentences = [s.text for s in doc.sentences if s.sentence_id in citing]
    for term in pick_terms(candidates, lang, title=doc.title, cited_sentences=cited_sentences):
        try:
            response = await gateway.submit(
                analysis_id, doc.doc_id, TermLookup(kind="term_lookup", term=term, lang=lang)
            )
        except GatewayError as exc:
            result.warnings.append(f"用語の照会を行えませんでした（{exc}）")
            break
        found = response.response.term if response.response else None
        if response.decision == "pending":
            result.pending.append(term)
            status: TermStatus = "pending"
        elif response.decision in ("sent", "cached"):
            result.resolved.append(term)
            status = "found"
        else:
            result.blocked += 1
            status = "failed" if response.decision == "failed" else "blocked"
        result.records.append(
            TermLookupRecord(
                term=term,
                request_id=response.request_id,
                status=status,
                description=found.description if found else None,
                source=found.source if found else None,
                reason=response.reason if status in ("blocked", "failed") else None,
            )
        )
    return result
