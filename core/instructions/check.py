"""選択した提出要件を全件評価する。根拠のない箇所や欠落は注釈へ変換しない。"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, Field

from core.analyze.context import normalize, output_language
from core.ingest.models import Document, Paragraph
from core.instructions.models import Requirement, RequirementResult, Snapshot, Verdict
from core.llm.budget import TokenBudget, estimate_messages_tokens
from core.llm.client import OitomaClient, OitomaError
from core.llm.prompts import load_prompt

ROLE = "requirement_check"
ResultCallback = Callable[[RequirementResult, int, int], Awaitable[None]]


class Assessment(BaseModel):
    verdict: Verdict
    reason: str = Field(min_length=1)
    sentence_ids: list[str] = Field(default_factory=list)
    paragraph_ids: list[str] = Field(default_factory=list)
    quote: str = ""
    suggestion: str = ""


def _paragraphs(doc: Document, scope: Literal["body", "all"]) -> list[Paragraph]:
    if scope == "all":
        return list(doc.paragraphs)
    roles = {s.section_id: s.role for s in doc.sections}
    return [
        p
        for p in doc.paragraphs
        if p.kind == "body"
        and roles.get(p.section_id or "") not in ("references", "biography", "acknowledgements")
    ]


def _result(req: Requirement, verdict: Verdict, reason: str, **kwargs: object) -> RequirementResult:
    return RequirementResult.model_validate(
        {
            "requirement_id": req.requirement_id,
            "verdict": verdict,
            "reason": reason,
            **kwargs,
        }
    )


def mechanical_check(req: Requirement, doc: Document) -> RequirementResult | None:
    if req.kind == "manual":
        return _result(
            req,
            "unknown",
            "この条件は自動判定に未対応です。提出先の指定と手元の文書で確認してください。",
        )
    if req.kind not in ("character_count", "reference_count", "heading"):
        return None
    if not doc.paragraphs or not any(p.text.strip() for p in doc.paragraphs):
        return _result(req, "unknown", "本文を抽出できなかったため確認できません。")
    pages = {p.page for p in doc.paragraphs if p.text.strip()}
    complete = len(pages) == doc.page_count
    if req.kind == "heading":
        matches = [s for s in doc.sections if normalize(req.value or "") == normalize(s.title)]
        if matches:
            return _result(
                req,
                "met",
                f"指定された見出し「{req.value}」を確認しました。",
                pages=sorted({s.page for s in matches}),
                coverage="full",
            )
        return _result(
            req,
            "unknown" if doc.warnings or not complete else "not_met",
            f"抽出された見出しに「{req.value}」がありません。PDFの見出し抽出には制約があります。",
            coverage="full" if complete else "partial",
        )
    if req.kind == "character_count":
        text = "".join(p.text for p in _paragraphs(doc, req.count_scope))
        value = len(text if req.include_spaces else re.sub(r"\s", "", text))
        scope = "抽出本文（見出し・参考文献を除く）" if req.count_scope == "body" else "全抽出段落"
        spaces = "を含む" if req.include_spaces else "を除く"
        reason = f"{scope}は{value:,}文字です（空白{spaces}）。PDF抽出に基づく値です。"
        if not complete:
            return _result(
                req, "unknown", reason + " 全ページの本文を確認できません。", coverage="partial"
            )
    else:
        value = len(doc.references)
        reason = f"構造化できた参考文献は{value}件です。"
        # 下限を既に満たす場合だけ、取りこぼしが結論を変えない。
        if (doc.warnings or not complete) and (
            req.maximum is not None or value < (req.minimum or 0)
        ):
            return _result(
                req,
                "unknown",
                reason + " 抽出に不確実な箇所があり、件数を確定できません。",
                coverage="partial",
            )
    ok = (req.minimum is None or value >= req.minimum) and (
        req.maximum is None or value <= req.maximum
    )
    return _result(req, "met" if ok else "not_met", reason, coverage="full")


def _validate_evidence(output: Assessment, doc: Document, chunk: str) -> RequirementResult:
    sentences = {s.sentence_id: s for s in doc.sentences}
    paragraphs = {p.paragraph_id: p for p in doc.paragraphs}
    sids = [s for s in output.sentence_ids if s in sentences and f"【{s}】" in chunk]
    pids = [p for p in output.paragraph_ids if p in paragraphs and f"[{p}]" in chunk]
    texts = [sentences[s].text for s in sids] + [paragraphs[p].text for p in pids]
    valid = (
        bool(output.quote.strip())
        and any(normalize(output.quote) in normalize(t) for t in texts)
        and normalize(output.quote) in normalize(chunk)
    )
    if not valid:
        sids, pids = [], []
    verdict = output.verdict
    claimed_evidence = bool(output.sentence_ids or output.paragraph_ids or output.quote)
    if (output.verdict == "met" or claimed_evidence) and not valid:
        verdict = "unknown"
    return RequirementResult(
        requirement_id="",
        verdict=verdict,
        reason=output.reason
        if verdict == output.verdict
        else "判定の根拠となる原文を確認できませんでした。",
        sentence_ids=sids,
        paragraph_ids=pids,
        quote=output.quote if valid else "",
        pages=sorted(
            {b.page for s in sids for b in sentences[s].bboxes} | {paragraphs[p].page for p in pids}
        ),
        coverage="partial",
        suggestion=output.suggestion if verdict != "unknown" else "",
    )


async def _content_check(
    client: OitomaClient,
    doc: Document,
    req: Requirement,
    out_lang: str,
) -> RequirementResult:
    system = load_prompt("requirement_check", doc.lang, output_language=out_lang)
    requirement = req.model_dump(exclude={"enabled", "source_excerpt"})
    base = json.dumps(
        {"requirement": requirement, "full_document": False, "manuscript": ""}, ensure_ascii=False
    )
    fixed = estimate_messages_tokens(
        [{"role": "system", "content": system}, {"role": "user", "content": base}]
    )
    cfg = client.config
    budget = TokenBudget(
        cfg.context_window, cfg.role(ROLE).max_tokens or cfg.api.default_max_tokens
    )
    by_id = {s.sentence_id: s for s in doc.sentences}
    blocks = [
        f"[{p.paragraph_id}] "
        + (
            "".join(f"【{sid}】{by_id[sid].text}" for sid in p.sentence_ids if sid in by_id)
            or p.text
        )
        for p in doc.paragraphs
    ]
    if not blocks:
        return _result(req, "unknown", "確認できる本文がありません。")
    batches = budget.pack(blocks, fixed_tokens=fixed + 500)

    async def judge(batch: list[str]) -> RequirementResult:
        chunk = "\n".join(batch)
        payload = json.dumps(
            {"requirement": requirement, "full_document": len(batches) == 1, "manuscript": chunk},
            ensure_ascii=False,
        )
        output = await client.chat_json(
            ROLE,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": payload},
            ],
            Assessment,
            lang=doc.lang,
        )
        return _validate_evidence(output, doc, chunk)

    results = await asyncio.gather(*(judge(batch) for batch in batches))
    complete = len({p.page for p in doc.paragraphs}) == doc.page_count
    absence_safe = complete and not doc.warnings
    if len(results) == 1:
        chosen = results[0]
        if chosen.verdict == "not_met" and not chosen.quote and not absence_safe:
            return _result(
                req,
                "unknown",
                "抽出結果に不確実な箇所があり、文書全体の不足を確定できません。",
                coverage="partial",
            )
    elif req.aggregation == "any":
        chosen = next((r for r in results if r.verdict == "met"), results[0])
        if chosen.verdict != "met":
            # 抜粋だけの欠落を全体の違反にはしない。
            return _result(
                req,
                "unknown",
                "文書を分割して確認しましたが、この要件を満たす根拠を確定できません。",
                coverage="full" if complete else "partial",
            )
    else:
        chosen = next((r for r in results if r.verdict == "not_met" and r.quote), results[0])
        if not (chosen.verdict == "not_met" and chosen.quote):
            if all(r.verdict == "met" for r in results) and complete:
                chosen = results[0]
            else:
                return _result(
                    req,
                    "unknown",
                    "分割した確認結果から、この要件の充足を確定できません。",
                    coverage="full" if complete else "partial",
                )
    if chosen.verdict == "met" and req.aggregation == "all" and not complete:
        return _result(
            req,
            "unknown",
            "全ページの本文を確認できず、文書全体に当てはまる条件を確定できません。",
            coverage="partial",
        )
    return chosen.model_copy(
        update={"requirement_id": req.requirement_id, "coverage": "full" if complete else "partial"}
    )


async def check_requirements(
    client: OitomaClient,
    doc: Document,
    snapshot: Snapshot,
    *,
    output_lang: Literal["paper", "ja"] = "paper",
    on_result: ResultCallback | None = None,
) -> list[RequirementResult]:
    requirements = [r for r in snapshot.requirements if r.enabled]
    done = 0
    out_lang = output_language(doc.lang, doc.lang, output_lang)

    async def check(req: Requirement) -> RequirementResult:
        nonlocal done
        try:
            result = mechanical_check(req, doc)
            if result is None:
                result = await _content_check(client, doc, req, out_lang)
        except (OitomaError, ValueError):
            result = _result(
                req,
                "unknown",
                "判定を完了できませんでした。条件や本文を確認して再解析してください。",
            )
        done += 1
        if on_result:
            await on_result(result, done, len(requirements))
        return result

    return list(await asyncio.gather(*(check(r) for r in requirements)))
