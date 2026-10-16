"""ルールの LLM 判定（SPEC 3.3-3 の「ルール」）。節ごとに、当てはまる承認済みのルールで違反を判定する。

- 判定の単位は節（長い節は、段落のまとまりに分ける）。「はじめにで目的を述べる」のような節全体に関わる
  ルールを、段落ごとに判定すると誤検出になるため
- 当てはまるルール：対象セクションが節の役割と合うもの（「全体」はすべて、「図表」は図表に言及する段落を含む
  まとまり、「引用」は文献を引用している段落を含むまとまり）。原稿の言語に合わないルールは除く
- ルールが多い場合は、本文とルールの埋め込みの近さで上位だけを使う（1回のプロンプトを短く保つため）
- 判定は高速モデル（役割 rule_check）で1回だけ行う
"""

import math
import re

from core.analyze.context import Target, normalize, numbered_paragraph
from core.analyze.evidence import quote_matches
from core.analyze.models import Severity, _LLMOutput
from core.ingest.models import Document, Lang
from core.llm.budget import estimate_tokens
from core.llm.client import OitomaClient
from core.llm.prompts import load_prompt
from core.rules.mechanical import RuleViolation
from core.rules.models import SECTION_ROLES, Rule

ROLE = "rule_check"
EMBEDDING_ROLE = "retrieval"
# 1回の判定に使うルールの上限
MAX_RULES_PER_CHECK = 12
# 1回の判定に入れる本文のトークン数の目安（これを超える節は段落のまとまりに分ける）
MAX_TEXT_TOKENS = 4000
# 1回の判定で、1つのルールについて挙げる違反の上限
MAX_PER_RULE = 2
_FIGURE = re.compile(r"(図|表)\s*[0-9０-９]|\b(Fig\.|Figures?|Tables?)\s*[0-9]", re.IGNORECASE)


class RuleViolationItem(_LLMOutput):
    rule_id: str
    sentence_ids: list[str]
    quote: str
    issue: str
    rationale: str
    severity: Severity = "suggestion"


class RuleCheckOutput(_LLMOutput):
    violations: list[RuleViolationItem]


def group_targets(targets: list[Target], max_tokens: int = MAX_TEXT_TOKENS) -> list[list[Target]]:
    """解析の対象の段落を、節ごと（長い節は max_tokens 以下のまとまりごと）に分ける。順序は保つ。"""
    groups: list[list[Target]] = []
    used = 0
    for target in targets:
        cost = estimate_tokens(target.paragraph.text)
        same_section = bool(groups) and (
            groups[-1][-1].paragraph.section_id == target.paragraph.section_id
        )
        if same_section and used + cost <= max_tokens:
            groups[-1].append(target)
            used += cost
        else:
            groups.append([target])
            used = cost
    return groups


def applicable_rules(
    rules: list[Rule], group: list[Target], doc: Document, cited: set[str]
) -> list[Rule]:
    first = group[0]
    role = first.section.role if first.section else None
    texts = [t.paragraph.text for t in group]
    sentence_ids = {sid for t in group for sid in t.paragraph.sentence_ids}
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
            if any(_FIGURE.search(text) for text in texts):
                chosen.append(rule)
        elif section == "引用":
            if cited & sentence_ids:
                chosen.append(rule)
        elif role is not None and role in SECTION_ROLES.get(section, frozenset()):
            chosen.append(rule)
    return chosen


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


async def embed_missing_rules(
    client: OitomaClient, rules: list[Rule], rule_vectors: dict[str, list[float]]
) -> None:
    """編集で無効になった埋め込みを、現在のルール本文から作り直す。"""
    missing = [r for r in rules if r.rule_id not in rule_vectors]
    if missing:
        vectors = await client.embed([r.rule for r in missing], role=EMBEDDING_ROLE)
        rule_vectors.update({r.rule_id: v for r, v in zip(missing, vectors, strict=True)})


async def select_rules(
    client: OitomaClient,
    rules: list[Rule],
    group: list[Target],
    rule_vectors: dict[str, list[float]],
    *,
    limit: int = MAX_RULES_PER_CHECK,
) -> list[Rule]:
    """ルールが limit を超える場合は、本文の埋め込みに近い順に limit 件を選ぶ。"""
    if len(rules) <= limit:
        return rules
    await embed_missing_rules(client, rules, rule_vectors)
    text = "\n".join(t.paragraph.text for t in group)
    (vector,) = await client.embed([text], role=EMBEDDING_ROLE)
    ranked = sorted(rules, key=lambda r: -_cosine(vector, rule_vectors[r.rule_id]))
    return ranked[:limit]


async def check_rules(
    client: OitomaClient,
    group: list[Target],
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
                section=group[0].section_label,
                paragraph="\n\n".join(numbered_paragraph(t.sentences) for t in group),
                rules="\n".join(f"[{r.rule_id}] {r.rule}" for r in rules),
            ),
        },
    ]
    texts = {s.sentence_id: s for t in group for s in t.sentences}

    def validate(output: RuleCheckOutput) -> RuleCheckOutput:
        if output.violations and not any(item.rule_id in by_id for item in output.violations):
            raise ValueError("入力にあるルールIDを指定してください")
        for item in output.violations:
            if item.rule_id in by_id and not quote_matches(
                item.quote, (texts[sid].text for sid in item.sentence_ids if sid in texts)
            ):
                raise ValueError("ルールの指摘の文ID・原文引用を確認してください")
        return output

    output = await client.chat_json(
        ROLE, messages, RuleCheckOutput, lang=prompt_lang, validate=validate
    )
    violations: list[RuleViolation] = []
    seen: dict[str, int] = {}
    for item in output.violations:
        rule = by_id.get(item.rule_id)
        ids = [sid for sid in item.sentence_ids if sid in texts]
        if rule is None or not ids or seen.get(rule.rule_id, 0) >= MAX_PER_RULE:
            continue
        seen[rule.rule_id] = seen.get(rule.rule_id, 0) + 1
        joined = normalize("".join(texts[sid].text for sid in ids))
        quote = item.quote.strip()
        if not quote or normalize(quote) not in joined:
            continue
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
