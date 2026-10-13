"""段落間のチェック（接続の断絶・セクションの役割違反、SPEC 3.3-3）。

全段落の要約を並べて判定する。長い論文は区切って判定し、区切りの前後で1段落ずつ重ねる。
"""

import asyncio

from core.analyze.context import Target
from core.analyze.models import FlowCheckOutput, FlowIssue, ParagraphStructure
from core.analyze.paragraph_check import early_stop_for, samples_for
from core.analyze.voting import Voted, vote
from core.ingest.models import Lang
from core.llm.budget import estimate_tokens
from core.llm.client import JSONOutputError, OitomaClient
from core.llm.prompts import load_prompt

ROLE = "logic_check"


def paragraph_line(target: Target, structure: ParagraphStructure) -> str:
    connectives = "、".join(structure.connectives) if structure.connectives else "-"
    return (
        f"[{target.paragraph.paragraph_id}] {target.section_label} | "
        f"{structure.summary} | {connectives}"
    )


def chunk_lines(lines: list[str], max_tokens: int) -> list[list[int]]:
    """行の番号を、max_tokens 以下の束に分ける。束どうしは1行ずつ重ねる。"""
    chunks: list[list[int]] = []
    current: list[int] = []
    used = 0
    for i, line in enumerate(lines):
        cost = estimate_tokens(line) + 1
        if current and used + cost > max_tokens:
            chunks.append(current)
            current = [current[-1]]
            used = estimate_tokens(lines[current[0]]) + 1
        current.append(i)
        used += cost
    if current:
        chunks.append(current)
    return chunks


def clean_flow_issues(
    issues: list[FlowIssue], targets: list[Target], indexes: list[int]
) -> list[FlowIssue]:
    """束の外の段落IDを除き、新しいセクションの最初の段落での「断絶」は除く。"""
    position = {targets[i].paragraph.paragraph_id: i for i in indexes}
    cleaned: list[FlowIssue] = []
    for issue in issues:
        i = position.get(issue.paragraph_id)
        if i is None:
            continue
        if issue.type == "disconnection":
            if i == 0 or (i - 1) not in indexes:
                continue
            prev = targets[i - 1]
            if prev.paragraph.section_id != targets[i].paragraph.section_id:
                continue
            issue = issue.model_copy(update={"previous_paragraph_id": prev.paragraph.paragraph_id})
        else:
            issue = issue.model_copy(update={"previous_paragraph_id": None})
        cleaned.append(issue)
    return cleaned


def same_flow_issue(a: FlowIssue, b: FlowIssue) -> bool:
    return a.type == b.type and a.paragraph_id == b.paragraph_id


async def check_flow(
    client: OitomaClient,
    targets: list[Target],
    structures: dict[str, ParagraphStructure],
    *,
    prompt_lang: Lang,
    output_language: str,
) -> tuple[list[Voted[FlowIssue]], int, list[str]]:
    """段落間の問題（多数決後）、判定回数の上限 n、警告を返す。実際の回数は Voted.samples。"""
    if len(targets) < 2:
        return [], samples_for(client, ROLE), []
    system = load_prompt("flow_check", prompt_lang, output_language=output_language)
    lines = [paragraph_line(t, structures[t.paragraph.paragraph_id]) for t in targets]
    cfg = client.config
    max_output = cfg.role(ROLE).max_tokens or cfg.api.default_max_tokens
    limit = (cfg.context_window - max_output) // 2 - estimate_tokens(system)
    chunks = chunk_lines(lines, max(limit, 500))
    n = samples_for(client, ROLE)
    warnings: list[str] = []

    async def judge(indexes: list[int]) -> list[Voted[FlowIssue]]:
        content = load_prompt(
            "flow_check_input", prompt_lang, paragraphs="\n".join(lines[i] for i in indexes)
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]

        async def once() -> list[FlowIssue]:
            output = await client.chat_json(ROLE, messages, FlowCheckOutput, lang=prompt_lang)
            return clean_flow_issues(output.issues, targets, indexes)

        ballot = await vote(
            once, same=same_flow_issue, n=n, early_stop=early_stop_for(client, ROLE)
        )
        if ballot.all_failed:
            raise JSONOutputError("段落間の判定に失敗しました") from ballot.errors[0]
        return ballot.voted

    results = await asyncio.gather(*(judge(c) for c in chunks), return_exceptions=True)
    voted: list[Voted[FlowIssue]] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        if isinstance(result, BaseException):
            warnings.append(f"段落間のチェックの一部に失敗しました：{result}")
            continue
        for v in result:
            key = (v.item.type, v.item.paragraph_id)
            if key not in seen:  # 重ねた段落で同じ指摘が出た場合
                seen.add(key)
                voted.append(v)
    return voted, n, warnings
