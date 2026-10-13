"""指摘の種類・重大度の表示名（UI と注釈で共通。UI の文言は日本語、SPEC 6.8）。"""

from typing import Final

CATEGORY_LABELS: Final[dict[str, str]] = {
    "logic": "論理",
    "readability": "読みやすさ",
    "rule": "ルール違反",
    "citation": "引用",
    "lab": "研究室知識",
}

SUBCATEGORY_LABELS: Final[dict[str, str]] = {
    "premise_evidence_mismatch": "前提と根拠の不一致",
    "leap": "論理の飛躍",
    "overclaim": "主張の過大",
    "subject_predicate_mismatch": "主語と述語のねじれ",
    "ambiguous_reference": "指示語の曖昧さ",
    "disconnection": "段落間の接続の断絶",
    "section_role_violation": "セクションの役割違反",
    "bib_not_found": "書誌が見つからない",
    "bib_mismatch": "書誌の不一致",
    "citation_unsupported": "引用内容が支持されない",
    "rule_violation": "ルールへの違反",
    "abbreviation_definition": "略語の定義",
    "figure_table_reference": "図表の参照",
    "unit_consistency": "単位の表記ゆれ",
}

SEVERITY_LABELS: Final[dict[str, str]] = {
    "critical": "重大",
    "major": "要修正",
    "suggestion": "提案",
}

# 注釈の形（色以外で種類を区別するための説明。凡例に使う）
SHAPE_LABELS: Final[dict[str, str]] = {
    "logic": "下線",
    "readability": "網掛け",
    "rule": "波線",
    "citation": "枠",
    "lab": "点線の枠",
}


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def subcategory_label(subcategory: str) -> str:
    return SUBCATEGORY_LABELS.get(subcategory, subcategory)


def severity_label(severity: str) -> str:
    return SEVERITY_LABELS.get(severity, severity)
