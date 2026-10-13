"""ルール資料・研究室知識の資料（PDF・DOCX・Markdown・テキスト）の読み込みとチャンク分け。

資料は data_private の下に保存し、学外に出さない。DOCX は外部ライブラリを使わず、中の XML から本文を読む。
"""

import re
import zipfile
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

import pymupdf

from core.ingest.models import Lang
from core.llm.budget import estimate_tokens, split_text
from core.store.files import data_dir

DocumentKind = Literal["pdf", "docx", "markdown", "text"]
SUFFIXES: dict[str, DocumentKind] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
}
_FILE_SUFFIX: dict[DocumentKind, str] = {
    "pdf": ".pdf",
    "docx": ".docx",
    "markdown": ".md",
    "text": ".txt",
}
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# 展開後の document.xml の上限（壊れたファイル・圧縮爆弾への備え）
MAX_DOCX_XML_BYTES = 50 * 1024 * 1024


def document_path(folder: str, document_id: str, kind: DocumentKind) -> Path:
    """data_private/<folder>/<document_id>.<拡張子>。ID はパスに使うので形式を厳密に確かめる。"""
    if not re.fullmatch(r"[A-Z]{2}-[0-9a-f]{12}", document_id):
        raise ValueError("document_id の形式が不正です")
    return data_dir() / folder / f"{document_id}{_FILE_SUFFIX[kind]}"


def save_file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def looks_valid(kind: DocumentKind, data: bytes) -> bool:
    """拡張子と中身が合っているか（PDF は %PDF-、DOCX は ZIP）。"""
    if kind == "pdf":
        return data.startswith(b"%PDF-")
    if kind == "docx":
        return data.startswith(b"PK")
    return True


def read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("word/document.xml")
        if info.file_size > MAX_DOCX_XML_BYTES:
            raise ValueError("DOCX の本文が大きすぎます")
        root = ElementTree.fromstring(archive.read(info))
    paragraphs = []
    for p in root.iter(f"{_WORD_NS}p"):
        text = "".join(t.text or "" for t in p.iter(f"{_WORD_NS}t"))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def read_pages(path: Path, kind: DocumentKind) -> list[tuple[int | None, str]]:
    """(ページ番号, 本文) のリスト。DOCX・Markdown・テキストはページを持たないので None。"""
    if kind == "pdf":
        with pymupdf.open(path) as pdf:
            return [(i + 1, page.get_text()) for i, page in enumerate(pdf)]
    if kind == "docx":
        return [(None, read_docx(path))]
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return [(None, raw.decode(encoding))]
        except UnicodeDecodeError:
            continue
    return [(None, raw.decode("utf-8", errors="replace"))]


def guess_lang(text: str) -> Lang:
    return "ja" if len(_CJK.findall(text[:5000])) >= 20 else "en"


def chunk_pages(pages: list[tuple[int | None, str]], max_tokens: int) -> list[str]:
    """ページの印（[p.N]）を付けて連結し、max_tokens 以下のチャンクに分ける（ページの途中でも分ける）。"""
    pieces: list[str] = []
    for page, text in pages:
        body = text.strip()
        if not body:
            continue
        marker = f"[p.{page}]\n" if page is not None else ""
        for part in split_text(body, max_tokens):
            pieces.append(marker + part)
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for piece in pieces:
        cost = estimate_tokens(piece) + 1
        if current and used + cost > max_tokens:
            chunks.append("\n".join(current))
            current, used = [], 0
        current.append(piece)
        used += cost
    if current:
        chunks.append("\n".join(current))
    return chunks
