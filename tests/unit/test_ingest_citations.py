"""本文中の引用と参考文献の対応付け。"""

from core.ingest.citations import CitationResolver, expand_numbers
from core.ingest.models import Reference


def ref(ref_id: str, authors: list[str], surnames: list[str], year: int, **kw: str) -> Reference:
    return Reference(ref_id=ref_id, authors=authors, surnames=surnames, year=year, title="t", **kw)


REFS = [
    ref("R-001", ["John Smith", "Ann Lee"], ["Smith", "Lee"], 2020),
    ref("R-002", ["Ann Lee"], ["Lee"], 2019),
    ref("R-003", ["Junxian He"], ["He"], 2020),
    ref("R-004", ["Stephen King"], ["King"], 2020),
    ref("R-005", ["Rico Sennrich"], ["Sennrich"], 2016),
    ref("R-006", ["Rico Sennrich"], ["Sennrich"], 2016),
    ref("R-007", ["田中 太郎", "鈴木 花子"], ["田中", "鈴木"], 2021),
    ref("R-008", ["鈴木 花子"], ["鈴木"], 2019),
]


def find(text: str, refs: list[Reference] = REFS) -> list[tuple[str, list[str], bool]]:
    return [(m.marker, m.ref_ids, m.resolved) for m in CitationResolver(refs).find(text)]


def test_expand_numbers() -> None:
    assert expand_numbers("1, 3") == [1, 3]
    assert expand_numbers("2-5") == [2, 3, 4, 5]
    assert expand_numbers("2–4, 7") == [2, 3, 4, 7]


def test_numeric_citations() -> None:
    assert find("As shown in [1, 3] and [2-3].") == [
        ("[1, 3]", ["R-001", "R-003"], True),
        ("[2-3]", ["R-002", "R-003"], True),
    ]
    assert find("Missing [99].") == [("[99]", [], False)]


def test_numeric_citations_use_labels() -> None:
    refs = [Reference(ref_id="R-001", label="7", title="x")]
    assert find("See [7].", refs) == [("[7]", ["R-001"], True)]


def test_parenthetical_author_year() -> None:
    assert find("(e.g., Lee, 2019; Smith et al., 2020)") == [
        ("(e.g., Lee, 2019; Smith et al., 2020)", ["R-002", "R-001"], True)
    ]


def test_narrative_author_year() -> None:
    assert find("Smith and Lee (2020) proposed it.") == [("Smith and Lee (2020)", ["R-001"], True)]


def test_short_surname_does_not_match_substring() -> None:
    # "He" が "Stephen" の一部に一致してはいけない
    assert find("He et al. (2020) showed it.") == [("He et al. (2020)", ["R-003"], True)]


def test_year_suffix_disambiguates() -> None:
    assert find("(Sennrich et al., 2016b)") == [("(Sennrich et al., 2016b)", ["R-006"], True)]
    assert find("(Sennrich et al., 2016)") == [("(Sennrich et al., 2016)", [], False)]


def test_partial_resolution_is_flagged() -> None:
    assert find("(Smith, 2020; Nobody, 2018)") == [
        ("(Smith, 2020; Nobody, 2018)", ["R-001"], False)
    ]


def test_japanese_citations() -> None:
    assert find("田中ら（2021）は手法を示した．") == [("田中ら（2021）", ["R-007"], True)]
    assert find("田中・鈴木（2021）が示した．") == [("田中・鈴木（2021）", ["R-007"], True)]
    assert find("先行研究（鈴木, 2019；田中 2021）がある．") == [
        ("（鈴木, 2019；田中 2021）", ["R-008", "R-007"], True)
    ]


def test_japanese_name_absorbing_preceding_word() -> None:
    assert find("本研究田中（2021）") == [("本研究田中（2021）", ["R-007"], True)]


def test_falls_back_to_other_authors() -> None:
    # GROBID が氏名の順を誤り、引用された姓が第一著者に無い場合
    refs = [ref("R-001", ["Trevor Anthony", "Cohn", "Mirella Lapata"], ["Anthony", "Lapata"], 2009)]
    assert find("(Cohn and Lapata, 2009)", refs) == [("(Cohn and Lapata, 2009)", ["R-001"], True)]


def test_non_citations_are_ignored() -> None:
    assert find("in (Table 1, 2020) and (2020年) and the year 2020.") == []
