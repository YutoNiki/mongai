"""参考文献の品質確認・本文からの補完・高速モデルによる構造化。"""

import json
from typing import Any

import httpx
import respx

from core.ingest.models import Reference
from core.ingest.references import (
    clean_references,
    entries_from_text,
    extract_with_llm,
    is_valid,
    needs_fallback,
    pack_lines,
    supplement_from_text,
    surname_of,
)
from core.llm.client import DEFAULT_BASE_URL, OitomaClient
from core.llm.config import ModelsConfig


def test_validity() -> None:
    good = Reference(ref_id="R-001", surnames=["Smith"], year=2020, title="A")
    bio = Reference(ref_id="R-002", authors=["略歴 Mengsay"], venue="2022 年東京工業大学卒業")
    assert is_valid(good)
    assert not is_valid(bio)
    assert needs_fallback([])
    assert needs_fallback([good, bio])
    assert not needs_fallback([good] * 9 + [bio])
    assert [r.ref_id for r in clean_references([bio, good])] == ["R-001"]


def test_surname_of() -> None:
    assert surname_of("Takase, S.") == "Takase"
    assert surname_of("Sho Takase") == "Takase"
    assert surname_of("高瀬 翔") == "高瀬"
    assert surname_of("") == ""


def test_entries_from_text_formats() -> None:
    entries = entries_from_text(
        [
            "Bahdanau, D., Cho, K., and Bengio, Y. (2015). Neural machine translation.",
            "Alexander M. Rush, Sumit Chopra, and Jason Weston. 2015. A neural attention model.",
            "Jiajun Zhang and Chengqing Zong. 2016. Exploiting source-side data.",
            "Sosuke Kobayashi. 2018. Contextual augmentation.",
            "[12] Smith J, Lee K. Title. 2019.",
            "高瀬 翔，岡崎 直観 (2021). 日本語の論文．",
            "continuation of the previous entry without author",
        ]
    )
    assert [(e[0], e[1], e[2]) for e in entries] == [
        (None, "Bahdanau", 2015),
        (None, "Rush", 2015),
        (None, "Zhang", 2016),
        (None, "Kobayashi", 2018),
        ("12", "Smith", 2019),
        (None, "高瀬", 2021),
    ]
    assert entries[-1][3].endswith("continuation of the previous entry without author")


def test_supplement_skips_known_and_scrambled_names() -> None:
    known = [
        Reference(
            ref_id="R-001",
            surnames=["Anthony"],
            authors=["Trevor Anthony", "Cohn"],
            year=2009,
            title="Sentence compression",
        ),
    ]
    text = [
        "Trevor Anthony Cohn and Mirella Lapata. 2009. Sentence compression.",
        "Isaac Caswell, Ciprian Chelba. 2019. Tagged back-translation.",
    ]
    merged = supplement_from_text(known, text)
    assert [(r.ref_id, r.surnames, r.source) for r in merged] == [
        ("R-001", ["Anthony"], "grobid"),
        ("R-002", ["Caswell"], "layout"),
    ]


def test_pack_lines_keeps_order_and_limit() -> None:
    lines = [f"Entry {i} " + "x" * 200 for i in range(30)]
    batches = pack_lines(lines, max_tokens=300)
    assert [line for b in batches for line in b] == lines
    assert len(batches) > 1


@respx.mock
async def test_extract_with_llm() -> None:
    config = ModelsConfig.model_validate(
        {
            "api": {"max_retries": 0},
            "roles": {"reference_extraction": {"model": "fast-model"}},
            "embeddings": {},
        }
    )
    payload: dict[str, Any] = {
        "references": [
            {"label": "1", "authors": ["高瀬 翔", "岡崎 直観"], "title": "論文", "year": 2021},
            {"authors": ["Takase, S."], "title": "Paper", "venue": "ACL", "year": 2020},
        ]
    }
    route = respx.post(f"{DEFAULT_BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}
        )
    )
    async with OitomaClient(config, api_key="k") as client:
        refs = await extract_with_llm(client, ["高瀬 翔，岡崎 直観 (2021). 論文．"], "ja")
    assert [(r.ref_id, r.surnames, r.year, r.source) for r in refs] == [
        ("R-001", ["高瀬", "岡崎"], 2021, "llm"),
        ("R-002", ["Takase"], 2020, "llm"),
    ]
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "fast-model"
    assert "参考文献" in body["messages"][0]["content"]
