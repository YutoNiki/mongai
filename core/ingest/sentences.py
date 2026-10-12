"""文分割（SPEC 3.3-1）。日本語は句点ベースの自作関数、英語は pysbd。

どちらも (開始, 終了) の文字オフセットを返し、元のテキストと座標の対応を保てるようにする。
"""

import re
from functools import lru_cache

import pysbd

from core.ingest.charclass import WIDE_CHARS
from core.ingest.models import Lang

Span = tuple[int, int]

_JA_TERMINALS = frozenset("。．！？!?")
# 文末記号の直後に続いても同じ文に含める閉じ括弧・引用符
_CLOSERS = frozenset("」』）)】］]〕〉》”’\"'")
_OPENERS: dict[str, frozenset[str]] = {
    "「": frozenset("」"),
    "『": frozenset("』"),
    "（": frozenset("）)"),
    "(": frozenset(")）"),
    "【": frozenset("】"),
    "［": frozenset("］]"),
    "[": frozenset("]］"),
    "〔": frozenset("〕"),
}
# 対応する閉じ括弧がこの文字数以内に無い開き括弧は無視する（箇条書きの "1)" などで分割が止まらないように）
_BRACKET_LOOKAHEAD = 200

_WIDE = re.compile(f"[{WIDE_CHARS}]")
_HIRAGANA = re.compile(f"[{chr(0x3041)}-{chr(0x309F)}]")


def detect_lang(text: str) -> Lang:
    """かな・漢字・全角記号の割合で日本語か英語かを判定する。"""
    letters = sum(1 for ch in text if not ch.isspace())
    if letters == 0:
        return "en"
    return "ja" if len(_WIDE.findall(text)) / letters >= 0.1 else "en"


def split_ja(text: str) -> list[Span]:
    """日本語の文分割。

    - 文末：「。」「．」「！」「？」（半角の ! ? を含む）。半角の "." は文末にしない（et al. 等）
    - 括弧・かぎ括弧の中では分割しない（対応する閉じ括弧が近くに無い場合を除く）
    - 数字に挟まれた「．」（3．5 など）は小数点として扱う
    - 文末記号の直後の閉じ括弧・連続する文末記号は、同じ文に含める
    - 「…。」の後は、ひらがなが続く場合（「…。」と言った）を除いて区切る
    """
    spans: list[Span] = []
    stack: list[frozenset[str]] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if stack and ch in stack[-1]:
            stack.pop()
            # 「…。」のように閉じ括弧の直前で文が終わっている場合は、後ろがひらがな（「…。」と言った）
            # でなければ、閉じ括弧の後で文を区切る
            if not stack and i > 0 and text[i - 1] in _JA_TERMINALS:
                j = i + 1
                while j < n and (text[j] in _JA_TERMINALS or text[j] in _CLOSERS):
                    j += 1
                if j < n and not _HIRAGANA.match(text[j]):
                    spans.append((start, j))
                    start = j
                    i = j
                    continue
        elif ch in _OPENERS:
            closers = _OPENERS[ch]
            window = text[i + 1 : i + 1 + _BRACKET_LOOKAHEAD]
            if any(c in window for c in closers):
                stack.append(closers)
        elif ch in _JA_TERMINALS and not stack:
            if ch == "．" and 0 < i < n - 1 and text[i - 1].isdigit() and text[i + 1].isdigit():
                i += 1
                continue
            j = i + 1
            while j < n and (text[j] in _JA_TERMINALS or text[j] in _CLOSERS):
                j += 1
            spans.append((start, j))
            start = j
            i = j
            continue
        i += 1
    if start < n:
        spans.append((start, n))
    return _trim(text, spans)


@lru_cache(maxsize=1)
def _en_segmenter() -> pysbd.Segmenter:
    return pysbd.Segmenter(language="en", clean=False, char_span=True)


def split_en(text: str) -> list[Span]:
    """英語の文分割（pysbd）。オフセットは元のテキストで検証し直す。"""
    spans: list[Span] = []
    cursor = 0
    for seg in _en_segmenter().segment(text):
        sentence = seg.sent.strip()
        if not sentence:
            continue
        found = text.find(sentence, cursor)
        if found == -1:  # pysbd が文字を変えた場合は、残り全体を1文にする
            spans.append((cursor, len(text)))
            cursor = len(text)
            break
        spans.append((found, found + len(sentence)))
        cursor = found + len(sentence)
    if cursor < len(text) and text[cursor:].strip():
        spans.append((cursor, len(text)))
    return _trim(text, spans)


def split_sentences(text: str, lang: Lang | None = None) -> list[Span]:
    return split_ja(text) if (lang or detect_lang(text)) == "ja" else split_en(text)


def _trim(text: str, spans: list[Span]) -> list[Span]:
    result: list[Span] = []
    for start, end in spans:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            result.append((start, end))
    return result
