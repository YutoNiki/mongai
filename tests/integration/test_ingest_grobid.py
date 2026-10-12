"""GROBID（ローカルコンテナ）を使った構造化の結合テスト。make test-integration で実行する。"""

import os
from pathlib import Path

import httpx
import pytest

from core.ingest.models import Document
from core.ingest.pipeline import ingest_pdf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GROBID_URL = os.environ.get("GROBID_URL", "http://grobid:8070")


def _grobid_alive() -> bool:
    try:
        return httpx.get(f"{GROBID_URL}/api/isalive", timeout=5, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _grobid_alive(), reason="GROBID に接続できません"),
]


@pytest.fixture(scope="module")
async def en_doc() -> Document:
    return await ingest_pdf(FIXTURES / "en_extraphrase_naacl2022.pdf", grobid_url=GROBID_URL)


@pytest.fixture(scope="module")
async def ja_doc() -> Document:
    return await ingest_pdf(FIXTURES / "ja_extraphrase_jnlp2023.pdf", grobid_url=GROBID_URL)


async def test_en_with_grobid(en_doc: Document) -> None:
    assert en_doc.title == "ExtraPhrase: Efficient Data Augmentation for Abstractive Summarization"
    grobid_refs = [r for r in en_doc.references if r.source == "grobid"]
    assert len(grobid_refs) >= 35
    assert all(r.title or r.venue for r in grobid_refs)
    assert all(c.resolved for c in en_doc.citations)
    assert not [w for w in en_doc.warnings if "GROBID を使えません" in w]


async def test_ja_with_grobid(ja_doc: Document) -> None:
    assert ja_doc.title == "ExtraPhrase: 生成型要約のための効率的データ拡張"
    # GROBID の誤認識（著者略歴など）は除かれ、取りこぼしは本文から補われる
    assert not any("略歴" in " ".join(r.authors) for r in ja_doc.references)
    assert len(ja_doc.references) >= 33
    assert all(c.resolved for c in ja_doc.citations)
