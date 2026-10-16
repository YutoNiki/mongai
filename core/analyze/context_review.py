"""機械判定の候補を、原稿全体の文脈で確認する。原稿は学内LLMだけに渡す。"""

import asyncio
import json
from typing import Literal

from core.analyze.context import normalize
from core.analyze.evidence import quote_matches
from core.analyze.models import _LLMOutput
from core.analyze.templates import text as template
from core.ingest.models import Document, Lang
from core.llm.budget import TokenBudget, estimate_messages_tokens
from core.llm.client import OitomaClient
from core.llm.prompts import load_prompt
from core.rules.mechanical import RuleViolation, body_sentences

ROLE = "rule_check"


class ContextDecision(_LLMOutput):
    candidate_id: str
    status: Literal["valid", "resolved", "late_definition", "unknown"]
    sentence_ids: list[str]
    quote: str


class ContextOutput(_LLMOutput):
    results: list[ContextDecision]


async def review_context(
    client: OitomaClient, doc: Document, candidates: list[dict[str, str]], *, lang: Lang
) -> dict[str, str]:
    if not candidates:
        return {}
    system = load_prompt("context_review", lang)
    sentences = {s.sentence_id: s for s in body_sentences(doc)}
    for p in doc.paragraphs:
        if p.kind == "caption":
            sentences.update(
                {s.sentence_id: s for s in doc.sentences if s.paragraph_id == p.paragraph_id}
            )
    cfg = client.config
    budget = TokenBudget(
        cfg.context_window, cfg.role(ROLE).max_tokens or cfg.api.default_max_tokens
    )
    statuses: dict[str, list[str]] = {c["candidate_id"]: [] for c in candidates}

    async def check_group(group: list[dict[str, str]]) -> None:
        base = json.dumps({"candidates": group, "manuscript": ""}, ensure_ascii=False)
        fixed = estimate_messages_tokens(
            [{"role": "system", "content": system}, {"role": "user", "content": base}]
        )
        blocks = []
        for paragraph in doc.paragraphs:
            selected = [sentences[sid] for sid in paragraph.sentence_ids if sid in sentences]
            if selected or paragraph.kind == "caption":
                text = "".join(f"【{s.sentence_id}】{s.text}" for s in selected) or paragraph.text
                blocks.append(
                    f"[{paragraph.paragraph_id}; page={paragraph.page}; kind={paragraph.kind}] {text}"
                )
        chunks = budget.pack(blocks, fixed_tokens=fixed + 500)
        expected = {c["candidate_id"] for c in group}
        by_candidate = {c["candidate_id"]: c for c in group}

        async def judge(batch: list[str]) -> None:
            chunk = "\n".join(batch)

            def validate(output: ContextOutput) -> ContextOutput:
                ids = [r.candidate_id for r in output.results]
                if len(ids) != len(expected) or set(ids) != expected:
                    raise ValueError("context decision IDs do not match candidates")
                for item in output.results:
                    candidate = by_candidate[item.candidate_id]
                    if (
                        item.status == "late_definition"
                        and candidate["kind"] != "abbreviation_definition"
                    ):
                        raise ValueError("late definition only applies to abbreviations")
                    if item.status in {"resolved", "late_definition"} and (
                        not quote_matches(
                            item.quote,
                            [
                                sentences[s].text
                                for s in item.sentence_ids
                                if s in sentences and f"【{s}】" in chunk
                            ],
                        )
                        or normalize(item.quote) not in normalize(chunk)
                    ):
                        raise ValueError("context evidence is not in manuscript")
                return output

            output = await client.chat_json(
                ROLE,
                [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"candidates": group, "manuscript": chunk}, ensure_ascii=False
                        ),
                    },
                ],
                ContextOutput,
                lang=lang,
                validate=validate,
            )
            for item in output.results:
                statuses[item.candidate_id].append(item.status)

        results = await asyncio.gather(*(judge(chunk) for chunk in chunks), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result

    results = await asyncio.gather(
        *(check_group(candidates[i : i + 6]) for i in range(0, len(candidates), 6)),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return {
        key: "resolved"
        if "resolved" in values
        else "late_definition"
        if "late_definition" in values
        else "valid"
        if values and all(v == "valid" for v in values)
        else "unknown"
        for key, values in statuses.items()
    }


async def review_violations(
    client: OitomaClient, doc: Document, violations: list[RuleViolation], *, lang: Lang
) -> tuple[list[RuleViolation], list[str]]:
    contextual = [
        v
        for v in violations
        if v.subcategory in {"figure_table_reference", "abbreviation_definition"}
    ]
    candidates = [
        {
            "candidate_id": str(i),
            "kind": v.subcategory,
            "rule": v.rule.rule,
            "quote": v.quote,
            "issue": v.issue,
            "first_use_ids": ",".join(v.sentence_ids),
        }
        for i, v in enumerate(contextual)
    ]
    decisions = await review_context(client, doc, candidates, lang=lang)
    kept = [v for v in violations if v not in contextual]
    warnings = []
    for i, violation in enumerate(contextual):
        status = decisions[str(i)]
        if status in {"valid", "late_definition"}:
            violation.model = client.config.role(ROLE).model
            if status == "late_definition":
                violation.issue = template("abbr_late.issue", lang, acronym=violation.quote)
                violation.rationale = template("abbr_late.rationale", lang)
            kept.append(violation)
        elif status == "unknown":
            warnings.append(f"文脈から確認できなかったため未確認です：{violation.quote[:80]}")
    return kept, warnings
