"""テスト用の合成PDFを作る（実在の未発表論文は使わない）。"""

from dataclasses import dataclass
from pathlib import Path

import pymupdf

A4 = (595.0, 842.0)


@dataclass(frozen=True)
class Text:
    x: float
    y: float  # ベースライン
    text: str
    font: str = "helv"  # helv / hebo（太字）/ japan
    size: float = 10.0


def make_pdf(path: Path, pages: list[list[Text]]) -> Path:
    doc = pymupdf.open()
    for items in pages:
        page = doc.new_page(width=A4[0], height=A4[1])
        for item in items:
            page.insert_text((item.x, item.y), item.text, fontname=item.font, fontsize=item.size)
    doc.save(path)
    doc.close()
    return path


def lines(
    x: float,
    y: float,
    texts: list[str],
    *,
    font: str = "helv",
    size: float = 10.0,
    leading: float = 13.0,
    indent_first: float = 0.0,
) -> list[Text]:
    """行を縦に並べる。indent_first で段落の1行目を字下げする。"""
    return [
        Text(x + (indent_first if i == 0 else 0.0), y + i * leading, t, font, size)
        for i, t in enumerate(texts)
    ]
