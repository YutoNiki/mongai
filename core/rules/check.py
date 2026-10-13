"""ルールの LLM 判定（SPEC 3.3-3 の「ルール」）。段落ごとに、当てはまる承認済みのルールで違反を判定する。

- 当てはまるルール：対象セクションが段落のセクションの役割と合うもの（「全体」はすべて、「図表」は図表に言及する
  段落、「引用」は文献を引用している段落）。原稿の言語に合わないルールは除く
- ルールが多い場合は、段落とルールの埋め込みの近さで上位だけを使う（1回のプロンプトを短く保つため）
- 判定は高速モデル（役割 rule_check）で1回だけ行う
"""

import math
import re

from pydantic import Field

from core.analyze.context import Target, normalize, numbered_paragraph
from core.analyze.models import Severity, _LLMOutput
from core.ingest.models import Document, Lang
from core.llm.client import OitomaClient
from core.llm.prompts import load_prompt
from core.rules.mechanical import RuleViolation
from core.rules.models import SECTION_ROLES, Rule

ROLE = "rule_check"
EMBEDDING_ROLE = "retrieval"
# 1段落の判定に使うルールの上限
MAX_RULES_PER_PARAGRAPH = 12
_FIGURE = re.compile(r"(図|表)\s*[0-9０-９]|\b(Fig\.|Figures?|Tables?)\s*[0-9]", re.IGNORECASE)


class RuleViolationItem(_LLMOutput):
    rule_id: str
    sentence_ids: list[str]
    quote: str
    issue: str
    rationale: str
    severity: Severity = "suggestion"


class RuleCheckOutput(_LLMOutput):
    violations: list[RuleViolationItem] = Field(default_factory=list)


def applicable_rules(
    rules: list[Rule], target: Target, doc: Document, cited: set[str]
) -> list[Rule]:
    role = target.section.role if target.section else None
    text = target.paragraph.text
    chosen: list[Rule] = []
    for rule in rules:
        if rule.status != "approved" or rule.check_type != "llm":
            continue
        if rule.lang not in ("both", doc.lang):
            continue
        section = rule.target_section
        if section == "全体":
            chosen.append(rule)
        elif section == "図表":
            if _FIGURE.search(text):
                chosen.append(rule)
        elif section == "引用":
            if cited & set(target.paragraph.sentence_ids):
                chosen.append(rule)
        elif role is not None and role in SECTION_ROLES.get(section, frozenset()):
            chosen.append(rule)
    return chosen


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


async def select_rules(
    client: OitomaClient,
    rules: list[Rule],
    target: Target,
    rule_vectors: dict[str, list[float]],
    *,
    limit: int = MAX_RULES_PER_PARAGRAPH,
) -> list[Rule]:
    """ルールが limit を超える場合は、段落の埋め込みに近い順に limit 件を選ぶ。"""
    if len(rules) <= limit:
        return rules
    with_vectors = [r for r in rules if r.rule_id in rule_vectors]
    if not with_vectors:
        return rules[:limit]
    (vector,) = await client.embed([target.paragraph.text], role=EMBEDDING_ROLE)
    ranked = sorted(with_vectors, key=lambda r: -_cosine(vector, rule_vectors[r.rule_id]))
    return ranked[:limit]


async def check_rules(
    client: OitomaClient,
    target: Target,
    rules: list[Rule],
    *,
    prompt_lang: Lang,
    output_language: str,
) -> list[RuleViolation]:
    if not rules:
        return []
    by_id = {r.rule_id: r for r in rules}
    messages = [
        {
            "role": "system",
            "content": load_prompt("rule_check", prompt_lang, output_language=output_language),
        },
        {
            "role": "user",
            "content": load_prompt(
                "rule_check_input",
                prompt_lang,
                section=target.section_label,
                paragraph=numbered_paragraph(target.sentences),
                rules="\n".join(f"[{r.rule_id}] {r.rule}" for r in rules),
            ),
        },
    ]
    output = await client.chat_json(ROLE, messages, RuleCheckOutput, lang=prompt_lang)
    texts = {s.sentence_id: s for s in target.sentences}
    violations: list[RuleViolation] = []
    seen: set[str] = set()
    for item in output.violations:
        rule = by_id.get(item.rule_id)
        ids = [sid for sid in item.sentence_ids if sid in texts]
        if rule is None or not ids or rule.rule_id in seen:
            continue
        seen.add(rule.rule_id)
        joined = normalize("".join(texts[sid].text for sid in ids))
        quote = item.quote.strip()
        if not quote or normalize(quote) not in joined:
            quote = texts[ids[0]].text  # 原文に無い引用は、該当する文の全文に置き換える
        violations.append(
            RuleViolation(
                rule=rule,
                subcategory="rule_violation",
                severity=item.severity,
                sentence_ids=ids,
                bboxes=[b for sid in ids for b in texts[sid].bboxes],
                quote=quote,
                issue=item.issue,
                rationale=item.rationale,
                model=client.config.role(ROLE).model,
            )
        )
    return violations
