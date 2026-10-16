"""未知の用語の照会（SPEC 3.3-3 の「未知の用語」、M7）。

1. 高速モデル（役割 term_extraction）が、節ごとに「定義なしで使われていて、隣接分野の読者が調べたくなる
   一般的な専門用語」を選ぶ（著者の造語・既存の手法名・固有名詞・論文の主題の語は選ばない。
   タイトルに含まれる語は、コードでも除く）
2. 論文全体で最大 MAX_TERMS 語を、gateway に term_lookup（1語だけのスキーマ）として送る。
   学外の辞書（OpenAlex）は英語の見出しだけを持つので、日本語の論文では、モデルが同時に答えた英語の対応語で照会する
3. standard モードでは term_lookup は承認制なので、承認キューに積まれる。利用者が承認するまで学外には出ない。
   gateway は、原稿で定義された造語や、新規性に関わる主張に近い語を拒否する

学内の検索（direction・addi(c)tion・LOCAL RAG）は API の仕様が不明のため、まだ使っていない（TODO M7）。
照会の記録と結果（学外の辞書の説明）は解析ごとに保存し、ダッシュボードに表示する。指摘にはまだ使わない。
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError, model_validator

from core.analyze.context import Target, numbered_paragraph
from core.analyze.context_review import review_context
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


class TermCandidate(_LLMOutput):
    term: str
    english: str | None = None  # 英語の対応する用語（学外の辞書は英語の見出しだけを持つため）

    @model_validator(mode="before")
    @classmethod
    def _from_str(cls, value: Any) -> Any:
        return {"term": value} if isinstance(value, str) else value


class TermOutput(_LLMOutput):
    terms: list[TermCandidate]


def lookup_query(term: str, english: str | None, lang: Lang) -> tuple[str, Lang]:
    """照会に使う語と言語。日本語の論文では、英語の対応語があればそれを使う（無ければ日本語のまま）。"""
    if lang == "ja" and english:
        candidate = english.strip()
        try:
            TermLookup(kind="term_lookup", term=candidate, lang="en")
        except ValidationError:
            return term, lang
        return candidate, "en"
    return term, lang


@dataclass
class TermLookupResult:
    # 承認待ちに積んだ用語（英語で照会したものは「用語→照会した語」）
    pending: list[str] = field(default_factory=list)
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
) -> list[TermCandidate]:
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
    english: dict[str, str] = {}
    for output in outputs:
        if isinstance(output, OitomaError):
            result.warnings.append(f"用語の抽出に失敗しました：{output}")
        elif isinstance(output, BaseException):
            raise output
        else:
            for c in output:
                candidates.append(c.term)
                if c.english:
                    english.setdefault(c.term.strip().casefold(), c.english)
    citing = {c.sentence_id for c in doc.citations}
    cited_sentences = [s.text for s in doc.sentences if s.sentence_id in citing]
    manuscript = " ".join(s.text for s in doc.sentences).casefold()
    chosen = pick_terms(
        [term for term in candidates if term.strip().casefold() in manuscript],
        lang,
        title=doc.title,
        cited_sentences=cited_sentences,
    )
    try:
        decisions = await review_context(
            client,
            doc,
            [
                {
                    "candidate_id": term,
                    "kind": "term",
                    "term": term,
                    "english": english.get(term.casefold(), ""),
                }
                for term in chosen
            ],
            lang=lang,
        )
    except OitomaError:
        result.warnings.append("用語の定義を文書全体で確認できなかったため、照会は未確認です")
        return result
    for term in chosen:
        if decisions[term] != "valid":
            if decisions[term] == "unknown":
                result.warnings.append(f"用語「{term}」の定義を確認できず、照会は未確認です")
            continue
        query, query_lang = lookup_query(term, english.get(term.casefold()), lang)
        try:
            response = await gateway.submit(
                analysis_id,
                doc.doc_id,
                TermLookup(kind="term_lookup", term=query, lang=query_lang),
            )
        except GatewayError as exc:
            result.warnings.append(f"用語の照会を行えませんでした（{exc}）")
            break
        found = response.response.term if response.response else None
        if response.decision == "pending":
            result.pending.append(term if query == term else f"{term}→{query}")
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
                query=query,
                request_id=response.request_id,
                status=status,
                matched=found.term if found else None,
                description=found.description if found else None,
                source=found.source if found else None,
                reason=response.reason if status in ("blocked", "failed") else None,
            )
        )
    return result
