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
from core.llm.budget import TokenBudget, estimate_messages_tokens, estimate_tokens, split_text
from core.llm.client import OitomaClient, OitomaError
from core.llm.prompts import load_prompt

ROLE = "requirement_check"
MAX_REQUIREMENTS_PER_BATCH = 4
ResultCallback = Callable[[RequirementResult, int, int], Awaitable[None]]


class Assessment(BaseModel):
    verdict: Verdict
    reason: str = Field(min_length=1)
    sentence_ids: list[str] = Field(default_factory=list)
    paragraph_ids: list[str] = Field(default_factory=list)
    quote: str = ""
    suggestion: str = ""


class BatchItem(Assessment):
    requirement_id: str


class BatchAssessment(BaseModel):
    results: list[BatchItem]


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


async def _content_checks(
    client: OitomaClient,
    doc: Document,
    requirements: list[Requirement],
    out_lang: str,
) -> list[RequirementResult]:
    system = load_prompt("requirement_check", doc.lang, output_language=out_lang)
    multiple = len(requirements) > 1
    if multiple:
        system += "\n" + load_prompt("requirement_batch", doc.lang)
    key = "requirements" if multiple else "requirement"
    values = [req.model_dump(exclude={"enabled", "source_excerpt"}) for req in requirements]
    requirement = values if multiple else values[0]
    base = json.dumps(
        {key: requirement, "full_document": False, "manuscript": ""}, ensure_ascii=False
    )
    fixed = estimate_messages_tokens(
        [{"role": "system", "content": system}, {"role": "user", "content": base}]
    )
    cfg = client.config
    budget = TokenBudget(
        cfg.context_window, cfg.role(ROLE).max_tokens or cfg.api.default_max_tokens
    )
    by_id = {s.sentence_id: s for s in doc.sentences}
    blocks: list[str] = []
    for paragraph in doc.paragraphs:
        prefix = f"[{paragraph.paragraph_id}] "
        pieces = [(f"【{sid}】", by_id[sid].text) for sid in paragraph.sentence_ids if sid in by_id]
        if not pieces:
            pieces = [("", paragraph.text)]
        for marker, text in pieces:
            available = budget.max_input_tokens - fixed - 500 - estimate_tokens(prefix + marker) - 2
            blocks.extend(prefix + marker + part for part in split_text(text, available))
    if not blocks:
        return [_result(req, "unknown", "確認できる本文がありません。") for req in requirements]
    batches = budget.pack(blocks, fixed_tokens=fixed + 500)

    async def judge(batch: list[str]) -> list[RequirementResult]:
        chunk = "\n".join(batch)
        payload = json.dumps(
            {key: requirement, "full_document": len(batches) == 1, "manuscript": chunk},
            ensure_ascii=False,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": payload},
        ]
        if multiple:

            def validate(output: BatchAssessment) -> BatchAssessment:
                ids = [item.requirement_id for item in output.results]
                if len(ids) != len(requirements) or set(ids) != {
                    r.requirement_id for r in requirements
                }:
                    raise ValueError("requirement result IDs are missing, duplicate or unexpected")
                return output

            output = await client.chat_json(
                ROLE, messages, BatchAssessment, lang=doc.lang, validate=validate
            )
            by_req = {item.requirement_id: item for item in output.results}
            return [
                _validate_evidence(by_req[req.requirement_id], doc, chunk) for req in requirements
            ]
        single = await client.chat_json(ROLE, messages, Assessment, lang=doc.lang)
        return [_validate_evidence(single, doc, chunk)]

    async def safe_judge(batch: list[str]) -> list[RequirementResult]:
        try:
            return await judge(batch)
        except (OitomaError, ValueError):
            return [
                _result(req, "unknown", "本文の一部の判定を完了できず、未確認です。")
                for req in requirements
            ]

    results = await asyncio.gather(*(safe_judge(batch) for batch in batches))
    return [
        _aggregate(req, doc, [chunk[i] for chunk in results]) for i, req in enumerate(requirements)
    ]


def _aggregate(
    req: Requirement, doc: Document, results: list[RequirementResult]
) -> RequirementResult:
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

    async def check(group: list[Requirement]) -> list[RequirementResult]:
        nonlocal done
        try:
            mechanical = mechanical_check(group[0], doc) if len(group) == 1 else None
            results = (
                [mechanical]
                if mechanical is not None
                else await _content_checks(client, doc, group, out_lang)
            )
        except (OitomaError, ValueError):
            results = [
                _result(
                    req,
                    "unknown",
                    "判定を完了できませんでした。条件や本文を確認して再解析してください。",
                )
                for req in group
            ]
        for result in results:
            done += 1
            if on_result:
                await on_result(result, done, len(requirements))
        return results

    content = [r for r in requirements if r.kind in ("content", "style")]
    groups = [[r] for r in requirements if r not in content]
    groups.extend(
        content[i : i + MAX_REQUIREMENTS_PER_BATCH]
        for i in range(0, len(content), MAX_REQUIREMENTS_PER_BATCH)
    )
    outputs = await asyncio.gather(*(check(group) for group in groups))
    by_id = {item.requirement_id: item for group in outputs for item in group}
    return [by_id[req.requirement_id] for req in requirements]
