"""見出しの番号の分解と、セクションの役割の判定。"""

import pytest

from core.ingest.models import SectionRole
from core.ingest.sections import classify, resolve_role, split_number


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("1 Introduction", ("1", "Introduction")),
        ("1はじめに", ("1", "はじめに")),
        ("2.1 文圧縮", ("2.1", "文圧縮")),
        ("4.1.1 文圧縮と言い換えの効果", ("4.1.1", "文圧縮と言い換えの効果")),
        ("A Quality of Back-translation", ("A", "Quality of Back-translation")),
        ("IV. Results", ("IV", "Results")),
        ("Abstract", (None, "Abstract")),
        ("Acknowledgements", (None, "Acknowledgements")),
        ("3", (None, "3")),
    ],
)
def test_split_number(title: str, expected: tuple[str | None, str]) -> None:
    assert split_number(title) == expected


@pytest.mark.parametrize(
    ("title", "role"),
    [
        ("Abstract", "abstract"),
        ("概要", "abstract"),
        ("Introduction", "introduction"),
        ("はじめに", "introduction"),
        ("Related Work", "related_work"),
        ("関連研究", "related_work"),
        ("Proposed Method: ExtraPhrase", "methods"),
        ("提案手法", "methods"),
        ("Experiments", "experiments"),
        ("実験", "experiments"),
        ("Results", "results"),
        ("Analysis", "discussion"),
        ("考察", "discussion"),
        ("Conclusion", "conclusion"),
        ("おわりに", "conclusion"),
        ("謝 辞", "acknowledgements"),
        ("References", "references"),
        ("参考文献", "references"),
        ("付録", "appendix"),
        ("略歴", "other"),
        ("Efficiency of Pseudo-data Generation", "other"),
    ],
)
def test_classify(title: str, role: SectionRole) -> None:
    assert classify(title) == role


def test_subsection_inherits_parent_role() -> None:
    known: dict[str, SectionRole] = {"3": "experiments"}
    assert resolve_role("3.2", "比較手法", known) == "experiments"
    assert resolve_role("3.2.1", "Details", known) == "experiments"


def test_distinct_roles_win_over_parent() -> None:
    known: dict[str, SectionRole] = {"3": "experiments"}
    assert resolve_role("3.4", "Results", known) == "results"
    assert resolve_role("3.5", "考察", known) == "discussion"


def test_letter_numbered_heading_is_appendix() -> None:
    assert resolve_role("A", "Quality of Back-translation", {}) == "appendix"
