"""未知の用語の照会：用語の選別と、gateway への照会（承認待ちに積まれる）。"""

import json
from typing import Any

import respx

from core.analyze.context import select_targets
from core.analyze.terms import MAX_TERMS, lookup_terms, pick_terms
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
        if request.term in self.blocked:
            return GatewayResponse(request_id="R-0", decision="blocked", reason="造語")
        return GatewayResponse(request_id="R-1", decision="pending", reason="承認待ち")


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
                [["到達圏の算出にはアイソクローンを用いた。", "配車は貪欲法で決める。"]],
            ),
        ]
    )
    targets = select_targets(doc, min_chars=1)
    gateway = QueueingGateway(blocked={"DRT-Opt"})
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=chat({"terms": ["アイソクローン", "DRT-Opt"]}))
        async with OitomaClient(config(), api_key="k") as client:
            result = await lookup_terms(
                client, gateway, targets, analysis_id="A-1", doc_id=doc.doc_id, lang="ja"
            )
        sent = json.loads(respx.calls[0].request.content)["messages"][1]["content"]
    assert "アイソクローン" in sent  # 段落を渡している
    assert result.pending == ["アイソクローン"] and result.blocked == 1
    assert gateway.requests[0] == {"kind": "term_lookup", "term": "アイソクローン", "lang": "ja"}
