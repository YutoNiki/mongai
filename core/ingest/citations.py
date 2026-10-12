"""本文中の引用と参考文献の対応付け（SPEC 3.3-1）。

対応する表記：
- 番号：[12]、[1, 3]、[2-5]、[2–5]
- 著者年（括弧内）：(Smith, 2020)、(Smith et al., 2020; Lee and Kim 2019a)、（田中, 2020；鈴木 2019）
- 著者年（地の文）：Smith et al. (2020)、Smith and Lee (2019)、田中ら（2020）、田中・鈴木（2019）
"""

import re
from dataclasses import dataclass

from core.ingest.charclass import JA_NAME_CHARS
from core.ingest.models import Reference

_YEAR = r"(?:19|20)\d{2}[a-z]?"
_NUMERIC = re.compile(r"\[(\s*\d{1,3}(?:\s*[-–,，]\s*\d{1,3})*\s*)\]")
_PAREN = re.compile(rf"[(（]([^()（）]{{0,400}}?{_YEAR}[^()（）]{{0,40}}?)[)）]")
_LATIN_NAME = r"[A-Z][A-Za-z'’\-]+"
_JA_NAME = rf"[{JA_NAME_CHARS}]{{1,8}}"
_JA_NAME_ONLY = re.compile(_JA_NAME)
_NARRATIVE = re.compile(
    rf"({_LATIN_NAME}|{_JA_NAME})"
    rf"(?:\s+et\s+al\.?|\s+(?:and|&)\s+{_LATIN_NAME}|\s*ら|\s*他|\s*ほか|・{_JA_NAME})?"
    rf"\s*[(（]({_YEAR})[)）]"
)
_ITEM_YEAR = re.compile(rf"({_YEAR})")
_ITEM_NAME = re.compile(rf"({_LATIN_NAME}|{_JA_NAME})")
_ITEM_PREFIX = re.compile(r"^(?:e\.g\.,?|i\.e\.,?|see|cf\.|例えば|参照)\s*", re.IGNORECASE)
# 著者名ではない語（括弧内の "Table 1, 2020" 等を誤認しないため）
_NOT_NAMES = frozenset({"Table", "Figure", "Fig", "Section", "Eq", "In", "See", "The", "This"})


@dataclass(frozen=True)
class CitationMatch:
    marker: str
    style: str  # "numeric" | "author_year"
    ref_ids: list[str]
    resolved: bool


def expand_numbers(body: str) -> list[int]:
    numbers: list[int] = []
    for part in re.split(r"[,，]", body):
        part = part.strip()
        if not part:
            continue
        bounds = re.split(r"\s*[-–]\s*", part)
        if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
            lo, hi = int(bounds[0]), int(bounds[1])
            if lo <= hi and hi - lo <= 50:
                numbers.extend(range(lo, hi + 1))
                continue
        if part.isdigit():
            numbers.append(int(part))
    return numbers


class CitationResolver:
    def __init__(self, references: list[Reference]) -> None:
        self.references = references
        self.by_label: dict[str, Reference] = {}
        for i, ref in enumerate(references):
            self.by_label[ref.label or str(i + 1)] = ref

    def find(self, text: str) -> list[CitationMatch]:
        matches: list[CitationMatch] = []
        for m in _NUMERIC.finditer(text):
            numbers = expand_numbers(m.group(1))
            refs = [self.by_label.get(str(n)) for n in numbers]
            ids = [r.ref_id for r in refs if r is not None]
            matches.append(
                CitationMatch(
                    m.group(0), "numeric", ids, bool(numbers) and len(ids) == len(numbers)
                )
            )

        narrative_spans: list[tuple[int, int]] = []
        for m in _NARRATIVE.finditer(text):
            name = m.group(1)
            if name in _NOT_NAMES:
                continue
            ref = self.match_author_year(name, m.group(2))
            narrative_spans.append(m.span())
            matches.append(
                CitationMatch(
                    m.group(0), "author_year", [ref.ref_id] if ref else [], ref is not None
                )
            )

        for m in _PAREN.finditer(text):
            if any(s <= m.start() < e for s, e in narrative_spans):
                continue
            items = [it.strip() for it in re.split(r"[;；]", m.group(1)) if it.strip()]
            item_ids: list[str] = []
            cited = 0
            for item in items:
                parsed = _parse_item(item)
                if parsed is None:
                    continue
                cited += 1
                ref = self.match_author_year(*parsed)
                if ref is not None:
                    item_ids.append(ref.ref_id)
            if cited:
                matches.append(
                    CitationMatch(m.group(0), "author_year", item_ids, len(item_ids) == cited)
                )
        return matches

    def match_author_year(self, name: str, year_text: str) -> Reference | None:
        # 日本語の姓は前の語を巻き込むことがある（"本研究田中"）ため、後ろから短くして照合し直す
        keys = [name]
        if _JA_NAME_ONLY.fullmatch(name):
            keys += [name[i:] for i in range(1, len(name) - 1)]
        for key in keys:
            ref = self._match_one(key, year_text)
            if ref is not None:
                return ref
        return None

    def _match_one(self, name: str, year_text: str) -> Reference | None:
        year = int(year_text[:4])
        suffix = year_text[4:]
        key = name.lower()
        same_year = [r for r in self.references if r.year == year]
        # 第一著者で照合し、見つからなければ他の著者でも照合する（GROBID が氏名の順を誤ることがある）
        candidates = [r for r in same_year if _is_author(key, r, first_only=True)]
        if not candidates:
            candidates = [r for r in same_year if _is_author(key, r, first_only=False)]
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if suffix:
            index = ord(suffix) - ord("a")
            if 0 <= index < len(candidates):
                return candidates[index]
        return None  # 同じ著者・年の文献が複数あり、区別できない


def _is_author(key: str, ref: Reference, *, first_only: bool) -> bool:
    surnames = ref.surnames[:1] if first_only else ref.surnames
    if any(s.lower() == key for s in surnames):
        return True
    for author in ref.authors[:1] if first_only else ref.authors:
        lowered = author.lower()
        if key in re.split(r"[\s,.]+", lowered):
            return True
        # 日本語の氏名は空白なしで書かれることがある（"高瀬翔"）ので、姓の前方一致で照合する
        if _JA_NAME_ONLY.match(key) and lowered.replace(" ", "").startswith(key):
            return True
    return False


def _parse_item(item: str) -> tuple[str, str] | None:
    """括弧内の1項目（"Smith et al., 2020" など）から (姓, 年) を取り出す。"""
    item = _ITEM_PREFIX.sub("", item)
    year = _ITEM_YEAR.search(item)
    if year is None:
        return None
    name = _ITEM_NAME.search(item[: year.start()])
    if name is None or name.group(1) in _NOT_NAMES:
        return None
    return name.group(1), year.group(1)
