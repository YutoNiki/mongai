"""プロンプトの読み込み。指示文は core/analyze/prompts/{ja,en}/ に置き、コードに埋め込まない。"""

from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Literal

PROMPT_DIR = Path(__file__).resolve().parents[1] / "analyze" / "prompts"

Lang = Literal["ja", "en"]


@lru_cache(maxsize=128)
def _read(name: str, lang: Lang) -> str:
    path = PROMPT_DIR / lang / f"{name}.md"
    if not path.is_file():
        path = PROMPT_DIR / lang / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip()


def load_prompt(name: str, lang: Lang, **values: str) -> str:
    """プロンプトを読み込み、$name 形式の差し込み（string.Template）を行う。"""
    template = Template(_read(name, lang))
    return template.substitute(values) if values else template.template
