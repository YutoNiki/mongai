"""原文との照合。モデルが作った引用を原文に差し替えて確定しない。"""

from collections.abc import Iterable

from core.analyze.context import normalize


def quote_matches(quote: str, texts: Iterable[str]) -> bool:
    quoted = normalize(quote)
    return bool(quoted) and any(quoted in normalize(text) for text in texts)
