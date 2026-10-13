"""再解析の差分（SPEC 3.5）：前回の解析と比べて、解消された指摘・新しい指摘・続いている指摘を求める。

論文を直すと文の番号（文ID）が変わるので、文IDではなく、指摘の種類が同じで、指摘の箇所（quote）の
文面が十分に近いものを同じ指摘とみなす。1つの指摘は、相手の1つの指摘とだけ対応付ける。
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

from core.analyze.context import normalize
from core.analyze.models import Finding

# 指摘の箇所の文面がこれ以上近ければ、同じ指摘とみなす（書き直しで少し変わった程度を許す）
SAME_QUOTE = 0.6


@dataclass(frozen=True)
class FindingsDiff:
    resolved: list[Finding]  # 前回はあったが、今回は無い（却下したものは除く）
    new: list[Finding]  # 今回だけにある
    persisting: list[tuple[Finding, Finding]]  # (今回, 前回)


def quote_similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def diff_findings(current: list[Finding], previous: list[Finding]) -> FindingsDiff:
    pairs: list[tuple[float, int, int]] = []
    for i, c in enumerate(current):
        for j, p in enumerate(previous):
            if c.subcategory != p.subcategory:
                continue
            similarity = quote_similarity(c.quote, p.quote)
            if similarity >= SAME_QUOTE:
                pairs.append((similarity, i, j))
    # 近いものから順に、1対1で対応付ける
    used_current: set[int] = set()
    used_previous: set[int] = set()
    matched: dict[int, int] = {}
    for _, i, j in sorted(pairs, key=lambda x: (-x[0], x[1], x[2])):
        if i in used_current or j in used_previous:
            continue
        used_current.add(i)
        used_previous.add(j)
        matched[i] = j
    persisting = [(current[i], previous[matched[i]]) for i in sorted(matched)]
    return FindingsDiff(
        resolved=[
            p for j, p in enumerate(previous) if j not in used_previous and p.status != "dismissed"
        ],
        new=[c for i, c in enumerate(current) if i not in used_current],
        persisting=persisting,
    )
