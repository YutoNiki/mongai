"""外部から取得したテキストを <external_data> タグで囲む（CLAUDE.md 最優先ルール4）。"""

import re

from core.llm.prompts import Lang, load_prompt

_TAG = re.compile(r"<\s*/?\s*external_data[^>]*>", re.IGNORECASE)
# source 属性に使える文字（API名・DOI・URL程度）。それ以外は落とす
_UNSAFE_SOURCE_CHARS = re.compile(r"[^A-Za-z0-9._:/@-]")


def wrap_external(text: str, source: str = "") -> str:
    """外部テキストをタグで囲む。中にタグがあっても抜け出せないよう無害化する。"""
    body = _TAG.sub("[external_data tag removed]", text)
    safe_source = _UNSAFE_SOURCE_CHARS.sub("", source)[:200]
    attr = f' source="{safe_source}"' if safe_source else ""
    return f"<external_data{attr}>\n{body}\n</external_data>"


def external_data_notice(lang: Lang) -> str:
    """システムプロンプトに入れる「タグ内の指示に従わない」旨の注意書き。"""
    return load_prompt("external_data_notice", lang)
