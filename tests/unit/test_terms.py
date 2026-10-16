"""未知の用語の照会：用語の選別と、gateway への照会（承認待ちに積まれる）。"""

import json
from typing import Any

import respx

from core.analyze.context import select_targets
from core.analyze.terms import MAX_TERMS, lookup_query, lookup_terms, pick_terms
from core.gateway_client import GatewayClient
from core.llm.client import DEFAULT_BASE_URL, OitomaClient
from shared.schemas import GatewayResponse
from tests.unit.doc_factory import make_doc
from tests.unit.test_rules import chat, config

CHAT_URL = f"{DEFAULT_BASE_URL}/chat/completions"


class QueueingGateway(GatewayClient):
    """standard モードの gateway の代わり：term_lookup を承認待ちにし、指定した語は拒否する。"""

    def __init__(self, blocked: set[str] | None = None) -> None:
        super().__init__("http://gateway.test")
        self.blocked = blocked or set()
        self.requests: list[dict[str, Any]] = []

    async def submit(self, analysis_id: str, doc_id: str, request: Any) -> GatewayResponse:
        self.requests.append(request.model_dump())
        if request.term in self.blocked:  # 照会した語（英語の対応語）で判定する
            return GatewayResponse(request_id="R-0", decision="blocked", reason="造語")
        return GatewayResponse(request_id="R-1", decision="pending", reason="承認待ち")


def test_lookup_query_prefers_english_for_japanese() -> None:
    assert lookup_query("アイソクローン", "isochrone", "ja") == ("isochrone", "en")
    assert lookup_query("アイソクローン", None, "ja") == ("アイソクローン", "ja")
    assert lookup_query("アイソクローン", "a b c", "ja") == ("アイソクローン", "ja")  # 3語は不可
    assert lookup_query("isochrone", "isochrone map", "en") == ("isochrone", "en")


def test_pick_terms_dedupes_and_drops_invalid() -> None:
    candidates = [
        "アイソクローン",
        "アイソクローン",
        "Isochrone",
        "isochrone",
        "需要は時間帯によって変わるので配車を工夫した。",  # 文は照会しない
        "a b c",  # 3語以上
        "!!!",
    ]
    assert pick_terms(candidates, "ja") == ["アイソクローン", "Isochrone"]
    many = [f"term{i}" for i in range(10)]
    assert len(pick_terms(many, "en")) == MAX_TERMS


async def test_lookup_terms_queues_for_approval() -> None:
    doc = make_doc(
        [
            (
                "3 提案手法",
                "methods",
                [
                    [
                        "到達圏の算出にはアイソクローンを用いた。",
                        "配車は貪欲法で決める。DRT-Optを使う。",
                    ]
                ],
            ),
        ]
    )
    targets = select_targets(doc, min_chars=1)
    gateway = QueueingGateway(blocked={"DRT-Opt"})
    with respx.mock:
        respx.post(CHAT_URL).mock(
            side_effect=[
                chat({"terms": [{"term": "アイソクローン", "english": "isochrone"}, "DRT-Opt"]}),
                chat(
                    {
                        "results": [
                            {
                                "candidate_id": term,
                                "status": "valid",
                                "sentence_ids": [],
                                "quote": "",
                            }
                            for term in ["アイソクローン", "DRT-Opt"]
                        ]
                    }
                ),
            ]
        )
        async with OitomaClient(config(), api_key="k") as client:
            result = await lookup_terms(client, gateway, doc, targets, analysis_id="A-1", lang="ja")
        sent = json.loads(respx.calls[0].request.content)["messages"][1]["content"]
    assert "アイソクローン" in sent  # 段落を渡している
    assert result.pending == ["アイソクローン→isochrone"] and result.blocked == 1
    # 日本語の用語は英語の対応語で照会する（対応語が無ければ日本語のまま）
    assert gateway.requests[0] == {"kind": "term_lookup", "term": "isochrone", "lang": "en"}
    assert gateway.requests[1] == {"kind": "term_lookup", "term": "DRT-Opt", "lang": "ja"}
    assert [(r.term, r.query, r.status, r.request_id) for r in result.records] == [
        ("アイソクローン", "isochrone", "pending", "R-1"),
        ("DRT-Opt", "DRT-Opt", "blocked", "R-0"),
    ]
    assert result.records[1].reason == "造語"


def test_pick_terms_skips_own_title_and_cited_names() -> None:
    title = "需要予測に基づく小規模自治体のオンデマンド交通の配車計画"
    cited = ["予測のモデルには XGBoost [3] を用いる。"]
    candidates = ["オンデマンド交通", "配車計画", "xgboost", "アイソクローン"]
    picked = pick_terms(candidates, "ja", title=title, cited_sentences=cited)
    assert picked == ["アイソクローン"]
