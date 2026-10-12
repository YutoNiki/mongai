"""参考文献の品質確認と、GROBID が失敗した場合の高速モデルによる構造化（SPEC 3.3-1）。"""

import asyncio
import logging
import re

from pydantic import BaseModel, ConfigDict, Field

from core.ingest.charclass import JA_LETTERS, JA_NAME_CHARS
from core.ingest.models import Lang, Reference
from core.llm.budget import estimate_tokens, split_text
from core.llm.client import OitomaClient
from core.llm.prompts import load_prompt

logger = logging.getLogger(__name__)

# GROBID の結果のうち、使える文献の割合がこれを下回ったら高速モデルで構造化し直す
MIN_VALID_RATIO = 0.8

_JA_NAME = re.compile(f"[{JA_LETTERS}]")


class ExtractedReference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str | None = None
    authors: list[str] = Field(default_factory=list)
    title: str | None = None
    venue: str | None = None
    year: int | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None


class ExtractedReferences(BaseModel):
    references: list[ExtractedReference]


def is_valid(ref: Reference) -> bool:
    """照合に使える程度に構造化できているか（著者・年・タイトルか掲載誌）。"""
    return (
        bool(ref.surnames or ref.authors) and ref.year is not None and bool(ref.title or ref.venue)
    )


def needs_fallback(refs: list[Reference]) -> bool:
    if not refs:
        return True
    return sum(is_valid(r) for r in refs) / len(refs) < MIN_VALID_RATIO


def surname_of(name: str) -> str:
    """著者名から照合用の姓を取り出す（"Takase, S." → Takase、"Sho Takase" → Takase、"高瀬 翔" → 高瀬）。"""
    name = name.strip()
    if "," in name:
        return name.split(",", 1)[0].strip()
    tokens = name.split()
    if not tokens:
        return ""
    if _JA_NAME.search(name):
        return tokens[0]  # 日本語の氏名は姓が先
    return tokens[-1]


def pack_lines(lines: list[str], max_tokens: int) -> list[list[str]]:
    """行を順序を保ったまま、max_tokens 以下の束に分ける（1行で超える場合は分割する）。"""
    batches: list[list[str]] = []
    current: list[str] = []
    used = 0
    for line in lines:
        for piece in split_text(line, max_tokens) if estimate_tokens(line) > max_tokens else [line]:
            cost = estimate_tokens(piece) + 1
            if current and used + cost > max_tokens:
                batches.append(current)
                current, used = [], 0
            current.append(piece)
            used += cost
    if current:
        batches.append(current)
    return batches


# 参考文献の1項目の書き出し："[12] ..."、"12. ..."、"Smith, J., ..."、"Smith J ..."、"高瀬 翔，..."
_ENTRY_LABEL = re.compile(r"^\s*\[?(\d{1,3})[\].]\s*")
# "Smith, J." / "Smith J" の形（姓が先）と、"John Smith," / "John A. Smith and ..." /
# "John Smith. 2018." の形（姓が後）
_ENTRY_SURNAME_FIRST = re.compile(r"^([A-Z][A-Za-z'’\-]+),?\s+[A-Z]\.?(?:[\s,.]|$)")
_ENTRY_FORENAME_FIRST = re.compile(
    r"^((?:[A-Z][A-Za-z'’\-]*\.?\s+){1,3}"  # 名（イニシャルを含む）
    r"(?:(?:van|von|de|der|da|di|le)\s+)?[A-Z][A-Za-z'’\-]+)"  # 姓（2文字以上）
    r"(?:,|\.\s|\s+and\s)"
)
_ENTRY_LATIN = re.compile(f"{_ENTRY_SURNAME_FIRST.pattern}|{_ENTRY_FORENAME_FIRST.pattern}")
_ENTRY_JA = re.compile(f"^([{JA_NAME_CHARS}]{{1,6}})[\\s　]")
_ENTRY_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})[a-z]?(?!\d)")


def entries_from_text(paragraphs: list[str]) -> list[tuple[str | None, str, int, str]]:
    """参考文献節の本文を項目に分け、(番号, 第一著者の姓, 年, 原文) を返す。

    書き出しが項目の形でない段落は、直前の項目の続き（字下げされた2行目以降）として連結する。
    """
    entries: list[list[str]] = []
    for text in paragraphs:
        body = _ENTRY_LABEL.sub("", text)
        starts = _ENTRY_LABEL.match(text) or _ENTRY_LATIN.match(body) or _ENTRY_JA.match(body)
        if starts or not entries:
            entries.append([text])
        else:
            entries[-1].append(text)
    result: list[tuple[str | None, str, int, str]] = []
    for parts in entries:
        raw = " ".join(parts)
        label_match = _ENTRY_LABEL.match(raw)
        body = _ENTRY_LABEL.sub("", raw)
        year = _ENTRY_YEAR.search(body)
        surname_first = _ENTRY_SURNAME_FIRST.match(body)
        forename_first = _ENTRY_FORENAME_FIRST.match(body)
        ja = _ENTRY_JA.match(body)
        # "Alexander M. Rush," を "Alexander, M." と誤読しないよう、姓が後の形を先に判定する
        if forename_first:
            surname = forename_first.group(1).split()[-1]
        elif surname_first:
            surname = surname_first.group(1)
        elif ja:
            surname = ja.group(1)
        else:
            continue
        if year is None:
            continue
        label = label_match.group(1) if label_match else None
        result.append((label, surname, int(year.group(1)), raw))
    return result


def supplement_from_text(refs: list[Reference], paragraphs: list[str]) -> list[Reference]:
    """GROBID が取りこぼした文献を、参考文献節の本文（著者・年・原文）から補う。"""
    # GROBID が氏名の順を誤ることがあるため、全著者の姓・氏名の語で重複を判定する
    known = {
        (token.lower(), r.year)
        for r in refs
        for name in [*r.surnames, *r.authors]
        for token in re.split(r"[\s,.]+", name)
        if token
    }
    added: list[Reference] = []
    for label, surname, year, raw in entries_from_text(paragraphs):
        if (surname.lower(), year) in known:
            continue
        known.add((surname.lower(), year))
        added.append(
            Reference(
                ref_id="",
                label=label,
                authors=[surname],
                surnames=[surname],
                year=year,
                raw=raw,
                source="layout",
            )
        )
    merged = refs + added
    return [r.model_copy(update={"ref_id": f"R-{i + 1:03d}"}) for i, r in enumerate(merged)]


def clean_references(refs: list[Reference]) -> list[Reference]:
    """使えない文献（著者略歴の誤認識など）を除き、ID を振り直す。順序は保つ。"""
    valid = [r for r in refs if is_valid(r)]
    return [r.model_copy(update={"ref_id": f"R-{i + 1:03d}"}) for i, r in enumerate(valid)]


async def extract_with_llm(
    client: OitomaClient, reference_text: list[str], lang: Lang
) -> list[Reference]:
    """参考文献節のテキストを高速モデルで構造化する。長い場合は分割して並列に処理する。"""
    system = load_prompt("reference_extraction", lang)
    role = client.config.role("reference_extraction")
    max_output = role.max_tokens or client.config.api.default_max_tokens
    # 出力（JSON）は入力より長くなるため、1回の入力を出力上限の半分程度に抑える
    batches = pack_lines(reference_text, max_tokens=max_output // 2)
    results = await asyncio.gather(
        *(
            client.chat_json(
                "reference_extraction",
                [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(b)}],
                ExtractedReferences,
                lang=lang,
            )
            for b in batches
        )
    )
    refs: list[Reference] = []
    for batch in results:
        for item in batch.references:
            refs.append(
                Reference(
                    ref_id=f"R-{len(refs) + 1:03d}",
                    label=item.label,
                    authors=item.authors,
                    surnames=[s for s in (surname_of(a) for a in item.authors) if s],
                    title=item.title,
                    venue=item.venue,
                    year=item.year,
                    volume=item.volume,
                    issue=item.issue,
                    pages=item.pages,
                    doi=item.doi,
                    source="llm",
                )
            )
    return refs
