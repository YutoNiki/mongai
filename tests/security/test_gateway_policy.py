"""検索ゲートウェイのセキュリティテスト（SPEC 2.7）。ネットワークなしで実行する（埋め込みと searcher は模擬）。

- 原稿の文を含む term_lookup・複数語の term_lookup・未定義フィールドを持つ要求を拒否する
- 原稿の新規性の主張に近い用語・原稿で定義された造語・原稿自身のタイトルを拒否する
- 運用モード（strict / standard / offline）、件数制限、監査ログ（追記専用）
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import gateway.main as gw
from gateway.audit import AuditStore
from gateway.config import GatewayConfig, Limits, get_config
from gateway.manuscripts import ManuscriptRegistry
from shared.schemas import SearchResponse

DOC = "6b5fa18bce71e989"
AID = "A-0123456789ab"
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

    def submit(self, request: dict[str, Any], **extra: Any) -> Any:
        return self.client.post(
            "/requests", json={"analysis_id": AID, "doc_id": DOC, "request": request, **extra}
        )


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
    return env.store.entries(limit=1)[0]


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
    r = env.client.post("/requests", json={"analysis_id": AID, "doc_id": DOC, **payload})
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
    queue = env.client.get("/queue").json()
    assert [q["request"]["term"] for q in queue] == ["BERT"]
    r = env.client.post(f"/queue/{queue[0]['request_id']}/approve").json()
    assert r["decision"] == "sent" and env.sent[-1]["term"] == "BERT"
    assert env.client.get("/queue").json() == []
    decisions = [e.decision for e in env.store.entries()]
    assert decisions == ["approved", "pending", "cached", "sent"]
    assert env.client.get("/audit/count", params={"analysis_id": AID}).json() == {"count": 2}


def test_strict_mode_queues_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(tmp_path, monkeypatch, GatewayConfig(mode="strict"))
    r = env.submit({"kind": "oa_fetch", "doi": "10.1000/abc"}).json()
    assert r["decision"] == "pending" and env.sent == []
    rejected = env.client.post(f"/queue/{r['request_id']}/reject").json()
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


def test_audit_log_is_append_only(env: Env) -> None:
    env.submit({"kind": "oa_fetch", "doi": "10.1000/abc"})
    conn = sqlite3.connect(env.store.path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE audit SET decision = 'sent'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM audit")
    conn.close()
