"""検索ゲートウェイのセキュリティテスト（SPEC 2.7）。ネットワークなしで実行する（埋め込みと searcher は模擬）。

- 原稿の文を含む term_lookup・複数語の term_lookup・未定義フィールドを持つ要求を拒否する
- 原稿の新規性の主張に近い用語・原稿で定義された造語・原稿自身のタイトルを拒否する
- 運用モード（strict / standard / offline）、件数制限、監査ログ（追記専用）
- 利用者（owner_id）ごとの分離：承認キュー・監査ログは本人の分だけ、承認待ちを別の利用者とまとめない、
  owner_id は searcher に渡さない
"""

import asyncio
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from fastapi.testclient import TestClient

import gateway.main as gw
from gateway.audit import AuditStore
from gateway.config import GatewayConfig, Limits, get_config
from gateway.manuscripts import ManuscriptRegistry
from shared.schemas import SearchResponse, TermResult

DOC = "6b5fa18bce71e989"
AID = "A-0123456789ab"
OWNER = "U-" + "a" * 32
OTHER = "U-" + "b" * 32
CLAIM = "抽出と言い換えを組み合わせて疑似データを安価に構築する新しい手法を提案する"
MANUSCRIPT = {
    "doc_id": DOC,
    "title": "ExtraPhrase: 生成型要約のための効率的データ拡張",
    "body": [
        "本研究では，抽出型要約と言い換えを組み合わせて疑似訓練データを構築する．",
        "提案手法は既存手法よりも低コストで効果的である．",
    ],
    "novelty_claims": [CLAIM],
    "coined_terms": ["ExtraPhrase"],
}


class Env:
    def __init__(self, client: TestClient, store: AuditStore, sent: list[dict[str, Any]]) -> None:
        self.client = client
        self.store = store
        self.sent = sent

    def submit(self, request: dict[str, Any], owner: str = OWNER, **extra: Any) -> Any:
        body = {"owner_id": owner, "analysis_id": AID, "doc_id": DOC, "request": request, **extra}
        return self.client.post("/requests", json=body)

    def queue(self, owner: str = OWNER) -> Any:
        return self.client.get("/queue", params={"owner_id": owner}).json()

    def decide(self, request_id: str, action: str, owner: str = OWNER) -> Any:
        return self.client.post(f"/queue/{request_id}/{action}", params={"owner_id": owner})


def make_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: GatewayConfig) -> Env:
    store = AuditStore(tmp_path / "audit.sqlite3")
    sent: list[dict[str, Any]] = []

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        # 新規性の主張そのもの・それを含む語は同じ向き、それ以外は直交するベクトル
        return [[1.0, 0.0] if t == CLAIM or "安価" in t else [0.0, 1.0] for t in texts]

    async def fake_send(request: dict[str, Any]) -> SearchResponse:
        sent.append(request)
        return SearchResponse(results=[])

    monkeypatch.setattr(gw, "embed", fake_embed)
    monkeypatch.setattr(gw, "send_to_searcher", fake_send)
    monkeypatch.setattr(gw, "_registry", ManuscriptRegistry())
    gw.app.dependency_overrides[gw.get_store] = lambda: store
    gw.app.dependency_overrides[gw.get_registry] = lambda: gw._registry
    gw.app.dependency_overrides[get_config] = lambda: config
    client = TestClient(gw.app)
    assert client.post("/manuscripts", json=MANUSCRIPT).status_code == 204
    return Env(client, store, sent)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    yield make_env(tmp_path, monkeypatch, GatewayConfig(mode="standard"))
    gw.app.dependency_overrides.clear()


def last_audit(env: Env) -> Any:
    """監査ログの最後の記録（利用者を問わない。拒否された要求には owner_id が無いことがある）。"""
    conn = sqlite3.connect(env.store.path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return type("Entry", (), dict(row))


# ---------------------------------------------------------------- term_lookup の拒否（SPEC 2.7）


@pytest.mark.parametrize(
    "term",
    [
        "本研究では，抽出型要約と言い換えを組み合わせて疑似訓練データを構築する．",  # 原稿の文そのもの
        "提案手法は既存手法よりも低コストで効果的である",  # 句読点を除いた原稿の文
        "抽出と言い換えを組み合わせて疑似データを安価に構築",  # 新規性の主張の一部
    ],
)
def test_term_lookup_with_manuscript_sentence_is_rejected(env: Env, term: str) -> None:
    r = env.submit({"kind": "term_lookup", "term": term, "lang": "ja"})
    assert r.status_code in (200, 422)
    body = r.json() if r.status_code == 200 else r.json()["detail"]
    assert body["decision"] == "blocked"
    assert env.sent == []
    assert last_audit(env).decision == "blocked"


@pytest.mark.parametrize(
    "term", ["neural data augmentation method", "low cost pseudo data generation", "a b c"]
)
def test_multi_word_term_lookup_is_rejected(env: Env, term: str) -> None:
    r = env.submit({"kind": "term_lookup", "term": term, "lang": "en"})
    assert r.status_code == 422
    assert r.json()["detail"]["decision"] == "blocked"
    entry = last_audit(env)
    assert entry.decision == "blocked" and "スキーマ違反" in (entry.reason or "")
    assert term not in str(entry.request)  # 拒否した要求の値は記録しない
    assert env.sent == []


@pytest.mark.parametrize(
    "payload",
    [
        {"request": {"kind": "term_lookup", "term": "BERT", "lang": "en", "context": "原稿の文"}},
        {"request": {"kind": "bib_lookup", "title": "A paper", "abstract": "原稿の要約"}},
        {"request": {"kind": "oa_fetch", "doi": "10.1000/x", "query": "free text"}},
        {"request": {"kind": "free_text", "query": "本研究の手法"}},
        {"request": {"kind": "oa_fetch", "doi": "10.1000/x"}, "note": "追加のフィールド"},
    ],
)
def test_undefined_fields_are_rejected(env: Env, payload: dict[str, Any]) -> None:
    r = env.client.post(
        "/requests", json={"owner_id": OWNER, "analysis_id": AID, "doc_id": DOC, **payload}
    )
    assert r.status_code == 422
    assert last_audit(env).decision == "blocked"
    assert env.sent == []


def test_term_close_to_novelty_claim_is_rejected(env: Env) -> None:
    # 助詞を含まない複合語でスキーマは通るが、新規性の主張と埋め込みが近いので拒否する
    r = env.submit({"kind": "term_lookup", "term": "安価疑似データ構築法", "lang": "ja"})
    assert r.json()["decision"] == "blocked" and "類似度" in r.json()["reason"]
    assert env.sent == []


def test_coined_term_and_own_title_are_rejected(env: Env) -> None:
    r = env.submit({"kind": "term_lookup", "term": "ExtraPhrase", "lang": "en"})
    assert r.json()["decision"] == "blocked" and "造語" in r.json()["reason"]
    for title in [
        "ExtraPhrase: 生成型要約のための効率的データ拡張",
        "生成型要約のための効率的データ拡張",  # 接頭辞を除いた原稿のタイトル
    ]:
        r = env.submit({"kind": "bib_lookup", "title": title})
        assert r.json()["decision"] == "blocked" and "タイトル" in r.json()["reason"]
    # 原稿のタイトルと語が重なるだけの、別の文献は照会できる
    r = env.submit(
        {"kind": "bib_lookup", "title": "Data augmentation for abstractive summarization"}
    )
    assert r.json()["decision"] == "sent"
    env.sent.clear()
    r = env.submit(
        {
            "kind": "bib_lookup",
            "title": "A cited paper",
            "first_author": "抽出型要約と言い換えを組み合わせて",
        }
    )
    assert r.json()["decision"] == "blocked"
    assert env.sent == []


def test_unregistered_manuscript_is_rejected(env: Env) -> None:
    r = env.client.post(
        "/requests",
        json={
            "owner_id": OWNER,
            "analysis_id": AID,
            "doc_id": "0" * 16,
            "request": {"kind": "oa_fetch", "doi": "10.1000/x"},
        },
    )
    assert r.json()["decision"] == "blocked"
    assert env.sent == []


# ---------------------------------------------------------------- 運用モード・件数制限・キャッシュ


def test_standard_mode(env: Env) -> None:
    bib = {
        "kind": "bib_lookup",
        "title": "Neural machine translation",
        "first_author": "Bahdanau",
        "year": 2015,
    }
    assert env.submit(bib).json()["decision"] == "sent"
    assert env.submit(bib).json()["decision"] == "cached"  # 同じ要求は送り直さない
    assert len(env.sent) == 1
    term = env.submit({"kind": "term_lookup", "term": "BERT", "lang": "en"}).json()
    assert term["decision"] == "pending"  # 用語の照会は承認制
    queue = env.queue()
    assert [q["request"]["term"] for q in queue] == ["BERT"]
    r = env.decide(queue[0]["request_id"], "approve").json()
    assert r["decision"] == "sent" and env.sent[-1]["term"] == "BERT"
    assert env.queue() == []
    decisions = [e.decision for e in env.store.entries(owner_id=OWNER)]
    assert decisions == ["approved", "pending", "cached", "sent"]
    count = env.client.get("/audit/count", params={"analysis_id": AID, "owner_id": OWNER})
    assert count.json() == {"count": 2}


def test_strict_mode_queues_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(tmp_path, monkeypatch, GatewayConfig(mode="strict"))
    r = env.submit({"kind": "oa_fetch", "doi": "10.1000/abc"}).json()
    assert r["decision"] == "pending" and env.sent == []
    rejected = env.decide(r["request_id"], "reject").json()
    assert rejected["decision"] == "rejected" and env.sent == []
    gw.app.dependency_overrides.clear()


def test_offline_mode_blocks_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(tmp_path, monkeypatch, GatewayConfig(mode="offline"))
    assert env.submit({"kind": "oa_fetch", "doi": "10.1000/abc"}).json()["decision"] == "blocked"
    assert env.sent == []
    gw.app.dependency_overrides.clear()


def test_request_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(tmp_path, monkeypatch, GatewayConfig(limits=Limits(max_requests_per_doc=2)))
    for i in range(2):
        assert env.submit({"kind": "oa_fetch", "doi": f"10.1000/a{i}"}).json()["decision"] == "sent"
    r = env.submit({"kind": "oa_fetch", "doi": "10.1000/a9"}).json()
    assert r["decision"] == "blocked" and "上限" in r["reason"]
    gw.app.dependency_overrides.clear()


def test_approvals_respect_limit(env: Env) -> None:
    gw.app.dependency_overrides[get_config] = lambda: GatewayConfig(
        mode="strict", limits=Limits(max_requests_per_doc=1)
    )
    ids = [
        env.submit({"kind": "oa_fetch", "doi": f"10.1000/a{i}"}).json()["request_id"]
        for i in range(2)
    ]
    assert env.decide(ids[0], "approve").json()["decision"] == "sent"
    assert env.decide(ids[1], "approve").json()["decision"] == "blocked"
    assert len(env.sent) == env.store.sent_count(DOC) == 1
    assert [item["request_id"] for item in env.queue()] == [ids[1]]


@pytest.mark.parametrize("mode", ["standard", "strict"])
async def test_concurrent_requests_respect_limit(
    env: Env, monkeypatch: pytest.MonkeyPatch, mode: Literal["standard", "strict"]
) -> None:
    config = GatewayConfig(mode=mode, limits=Limits(max_requests_per_doc=1))
    gw.app.dependency_overrides[get_config] = lambda: config

    async def delayed_send(request: dict[str, Any]) -> SearchResponse:
        env.sent.append(request)
        await asyncio.sleep(0.02)
        return SearchResponse(results=[])

    monkeypatch.setattr(gw, "send_to_searcher", delayed_send)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gw.app), base_url="http://test"
    ) as client:
        bodies = [
            {
                "owner_id": OWNER,
                "analysis_id": AID,
                "doc_id": DOC,
                "request": {"kind": "oa_fetch", "doi": f"10.1000/a{i}"},
            }
            for i in range(2)
        ]
        if mode == "strict":
            queued = [await client.post("/requests", json=body) for body in bodies]
            calls = [
                client.post(f"/queue/{r.json()['request_id']}/approve", params={"owner_id": OWNER})
                for r in queued
            ]
        else:
            calls = [client.post("/requests", json=body) for body in bodies]
        responses = await asyncio.gather(*calls)
    assert sorted(r.json()["decision"] for r in responses) == ["blocked", "sent"]
    assert len(env.sent) == env.store.sent_count(DOC) == 1


def test_send_reservations_are_atomic_across_connections(env: Env) -> None:
    def reserve(i: int) -> bool:
        return AuditStore(env.store.path).reserve_send(f"R-{i}", DOC, 2)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(12))) == 2
    assert AuditStore(env.store.path).sent_count(DOC) == 2


def test_repeated_approval_only_sends_once(env: Env) -> None:
    rid = env.submit({"kind": "term_lookup", "term": "BERT", "lang": "en"}).json()["request_id"]
    assert env.decide(rid, "approve").json()["decision"] == "sent"
    assert env.decide(rid, "approve").status_code == 404
    assert env.decide(rid, "reject").status_code == 404
    assert len(env.sent) == env.store.sent_count(DOC) == 1


def test_failed_sends_keep_their_reservation(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    gw.app.dependency_overrides[get_config] = lambda: GatewayConfig(
        limits=Limits(max_requests_per_doc=1)
    )

    async def fail(request: dict[str, Any]) -> SearchResponse:
        env.sent.append(request)
        raise httpx.ReadTimeout("synthetic failure")

    monkeypatch.setattr(gw, "send_to_searcher", fail)
    assert env.submit({"kind": "oa_fetch", "doi": "10.1000/a"}).json()["decision"] == "failed"
    assert env.submit({"kind": "oa_fetch", "doi": "10.1000/b"}).json()["decision"] == "blocked"
    assert len(env.sent) == env.store.sent_count(DOC) == 1
    assert [e.decision for e in env.store.entries(owner_id=OWNER)] == ["blocked", "failed"]


def test_reservations_keep_legacy_counts(env: Env) -> None:
    env.store.record(
        request_id="R-old",
        owner_id=OWNER,
        analysis_id=AID,
        doc_id=DOC,
        kind="oa_fetch",
        request={},
        decision="sent",
    )
    assert env.store.reserve_send("R-new", DOC, 2)
    env.store.record(
        request_id="R-new",
        owner_id=OWNER,
        analysis_id=AID,
        doc_id=DOC,
        kind="oa_fetch",
        request={},
        decision="approved",
    )
    reopened = AuditStore(env.store.path)
    assert reopened.sent_count(DOC) == 2
    assert not reopened.reserve_send("R-extra", DOC, 2)


def test_audit_log_is_append_only(env: Env) -> None:
    env.submit({"kind": "oa_fetch", "doi": "10.1000/abc"})
    conn = sqlite3.connect(env.store.path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE audit SET decision = 'sent'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM audit")
    conn.close()


def test_term_not_found_is_not_cached(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """見つからなかった用語は、承認して送っても結果をキャッシュしない（次の照会で改めて承認を求める）。"""
    term = {"kind": "term_lookup", "term": "Isochrone", "lang": "en"}
    assert env.submit(term).json()["decision"] == "pending"
    (item,) = env.queue()
    assert env.decide(item["request_id"], "approve").json()["decision"] == "sent"
    assert env.store.cached(term) is None
    assert env.submit(term).json()["decision"] == "pending"

    # 見つかった用語はキャッシュし、次からは送り直さない
    async def found(request: dict[str, Any]) -> SearchResponse:
        env.sent.append(request)
        return SearchResponse(term=TermResult(term="Isochrone map", source="openalex"))

    monkeypatch.setattr(gw, "send_to_searcher", found)
    (item,) = env.queue()
    env.decide(item["request_id"], "approve")
    assert env.submit(term).json()["decision"] == "cached"


# ---------------------------------------------------------------- 利用者ごとの分離


def test_request_without_owner_is_rejected(env: Env) -> None:
    r = env.client.post(
        "/requests",
        json={"analysis_id": AID, "doc_id": DOC, "request": {"kind": "oa_fetch", "doi": "10.1/x"}},
    )
    assert r.status_code == 422 and r.json()["detail"]["decision"] == "blocked"
    assert (
        env.client.post(
            "/requests",
            json={
                "owner_id": "alice@example.ac.jp",  # メールアドレスなど、内部ユーザーID以外は受け付けない
                "analysis_id": AID,
                "doc_id": DOC,
                "request": {"kind": "oa_fetch", "doi": "10.1/x"},
            },
        ).status_code
        == 422
    )
    assert env.sent == []


def test_queue_and_approval_are_per_owner(env: Env) -> None:
    term = {"kind": "term_lookup", "term": "BERT", "lang": "en"}
    mine = env.submit(term).json()["request_id"]
    theirs = env.submit(term, owner=OTHER).json()["request_id"]
    assert mine != theirs  # 別の利用者の同じ要求とはまとめない
    assert [q["request_id"] for q in env.queue()] == [mine]
    assert [q["request_id"] for q in env.queue(OTHER)] == [theirs]
    assert env.client.get("/queue").status_code == 422  # 利用者の指定が無い一覧は返さない
    # ほかの利用者の要求は承認・却下できない
    assert env.decide(theirs, "approve").status_code == 404
    assert env.decide(theirs, "reject").status_code == 404
    assert env.sent == []
    # 自分の要求を承認しても、ほかの利用者の要求は送られない
    assert env.decide(mine, "approve").json()["decision"] == "sent"
    assert len(env.sent) == 1 and [q["request_id"] for q in env.queue(OTHER)] == [theirs]


def test_audit_is_per_owner(env: Env) -> None:
    env.submit({"kind": "oa_fetch", "doi": "10.1000/mine"})
    env.submit({"kind": "oa_fetch", "doi": "10.1000/theirs"}, owner=OTHER)
    mine = env.client.get("/audit", params={"owner_id": OWNER}).json()
    assert [e["request"]["doi"] for e in mine] == ["10.1000/mine"]
    assert env.client.get("/audit").status_code == 422  # 全員の記録は返さない
    # owner_id を省くと、利用者のいない記録（共通デモ・認証の導入前）だけ
    assert env.client.get("/audit", params={"analysis_id": AID}).json() == []
    assert env.client.get("/audit/count", params={"analysis_id": AID}).json() == {"count": 0}


def test_owner_id_is_not_sent_to_searcher(env: Env) -> None:
    env.submit({"kind": "oa_fetch", "doi": "10.1000/abc"})
    assert env.sent and all("owner_id" not in r for r in env.sent)
