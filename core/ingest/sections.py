"""見出しからセクションを組み立て、役割（はじめに・手法・結果など）を判定する。"""

import re

from core.ingest.models import SectionRole

# 数字の番号は直後に空白が無くてもよい（"1はじめに"）。英字・ローマ数字の番号は空白が必要（"Abstract" を分けない）
_NUMBER = re.compile(
    r"^(?:(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s*|([A-H](?:\.\d{1,2}){0,2}|[IVX]{1,4})\.?\s+)"
)

# 先に一致したものを採用する（"Related Work" を "Work" より先に、など）
_ROLE_KEYWORDS: list[tuple[SectionRole, tuple[str, ...]]] = [
    ("abstract", ("abstract", "概要", "要旨", "あらまし")),
    ("acknowledgements", ("acknowledg", "謝辞")),
    ("references", ("references", "bibliography", "参考文献", "引用文献")),
    ("appendix", ("appendix", "付録")),
    ("other", ("biograph", "略歴", "著者紹介")),
    ("introduction", ("introduction", "はじめに", "序論", "まえがき", "緒言", "背景と目的")),
    ("related_work", ("related work", "background", "previous work", "関連研究", "先行研究")),
    ("conclusion", ("conclusion", "おわりに", "結論", "まとめ", "むすび", "結び")),
    ("discussion", ("discussion", "analysis", "考察", "分析", "議論")),
    ("results", ("result", "結果")),
    ("experiments", ("experiment", "evaluation", "setup", "実験", "評価")),
    ("methods", ("method", "approach", "proposed", "model", "手法", "提案", "方法", "モデル")),
]


_DISTINCT_ROLES: frozenset[SectionRole] = frozenset(
    {"results", "discussion", "conclusion", "related_work"}
)


def split_number(title: str) -> tuple[str | None, str]:
    """ "2.1 文圧縮" → ("2.1", "文圧縮")。"""
    m = _NUMBER.match(title.strip())
    if not m or m.end() == len(title.strip()):
        return None, title.strip()
    return m.group(1) or m.group(2), title.strip()[m.end() :].strip()


def classify(title: str) -> SectionRole:
    normalized = re.sub(r"\s+", "", title.lower())
    spaced = " ".join(title.lower().split())
    for role, keywords in _ROLE_KEYWORDS:
        for kw in keywords:
            if kw in spaced or kw.replace(" ", "") in normalized:
                return role
    return "other"


def parent_number(number: str) -> str | None:
    return number.rsplit(".", 1)[0] if "." in number else None


def resolve_role(number: str | None, title: str, known: dict[str, SectionRole]) -> SectionRole:
    """節の役割を決める。

    小見出し（"3.2" など）は親（"3"）の役割を優先する（"3.2 比較手法" は手法ではなく実験の一部）。
    ただし結果・考察・結論・関連研究は、小見出しでもその語を優先する（"3.4 Results"）。
    親の役割が決まらない場合と、大見出しは、見出しの語から判定する。
    """
    own = classify(title)
    if own in _DISTINCT_ROLES:
        return own
    if number:
        parent = parent_number(number)
        while parent is not None:
            if known.get(parent, "other") != "other":
                return known[parent]
            parent = parent_number(parent)
    if own == "other" and number and number[0] in "ABCDEFGH":
        return "appendix"
    return own
