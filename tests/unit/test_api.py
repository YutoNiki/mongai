"""ダッシュボード用 API（アップロード・解析の開始・集計・指摘・CSV）。"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.analyze.pipeline import AnalysisSettings
from core.api.main import app, get_gateway, get_lab_store, get_queue, get_rule_store, get_store
from core.gateway_client import GatewayClient
from core.ingest.models import BBox
from core.lab.models import LabComment, LabSource
from core.lab.store import LabStore
from core.llm.usage import UsageRow
from core.rules.store import RuleStore
from core.sample import SAMPLE_ANALYSIS_ID, SAMPLE_DOC_ID, install_sample
from core.store import Store, TermLookupRecord, today_jst
from shared.schemas import GatewayResponse, SearchResponse, TermResult
from tests.unit.doc_factory import make_doc
from tests.unit.pdf_factory import Text, make_pdf
from tests.unit.test_annotate import finding


class OfflineGateway(GatewayClient):
    """接続できない gateway（外部通信の件数は 0、ログは「利用できない」になる）。"""

    def __init__(self) -> None:
        super().__init__("http://127.0.0.1:9")


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, analysis_id: str) -> None:
        self.enqueued.append(analysis_id)

    async def enqueue_rules(self, document_id: str) -> None:
        self.enqueued.append(document_id)

    async def enqueue_lab(self, document_id: str) -> None:
        self.enqueued.append(document_id)


@pytest.fixture
def ctx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Store, FakeQueue, bytes]]:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    store = Store(tmp_path / "db.sqlite3")
    queue = FakeQueue()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_queue] = lambda: queue
    app.dependency_overrides[get_gateway] = lambda: OfflineGateway()
    rule_store = RuleStore(tmp_path / "db.sqlite3")
    app.dependency_overrides[get_rule_store] = lambda: rule_store
    lab_store = LabStore(tmp_path / "db.sqlite3")
    app.dependency_overrides[get_lab_store] = lambda: lab_store
    pdf = make_pdf(tmp_path / "p.pdf", [[Text(72, 120, "A synthetic paper.")]]).read_bytes()
    yield TestClient(app), store, queue, pdf
    app.dependency_overrides.clear()


def upload(client: TestClient, pdf: bytes, name: str = "論文.pdf") -> str:
    r = client.post("/api/documents", files={"file": (name, pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    return str(r.json()["doc_id"])


def test_upload_rejects_non_pdf(ctx: tuple[TestClient, Store, FakeQueue, bytes]) -> None:
    client, *_ = ctx
    r = client.post("/api/documents", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_upload_and_start_analysis(ctx: tuple[TestClient, Store, FakeQueue, bytes]) -> None:
    client, store, queue, pdf = ctx
    doc_id = upload(client, pdf, "../../evil/論文.pdf")
    assert store.get_paper(doc_id) is not None
    assert store.get_paper(doc_id).filename == "論文.pdf"  # type: ignore[union-attr]
    assert client.get(f"/api/documents/{doc_id}/paper.pdf").content == pdf

    r = client.post("/api/analyses", json={"doc_id": doc_id, "output_lang": "ja"})
    assert r.status_code == 201
    summary = r.json()
    aid = summary["analysis"]["analysis_id"]
    assert summary["analysis"]["status"] == "queued"
    assert summary["analysis"]["settings"]["output_lang"] == "ja"
    assert queue.enqueued == [aid]
    assert client.post("/api/analyses", json={"doc_id": "0" * 16}).status_code == 404
    assert [a["analysis_id"] for a in client.get("/api/analyses").json()] == [aid]
    store.set_progress(aid, "paragraph_check", 3, 10)
    listed = client.get("/api/analyses").json()[0]
    assert listed["progress"] == {"stage": "paragraph_check", "done": 3, "total": 10}


def seed(store: Store, doc_id: str) -> str:
    doc = make_doc(
        [
            ("1 はじめに", "introduction", [["文．"]]),
            ("2 手法", "methods", [["文．"]]),
            ("2.1 詳細", "methods", [["文．"]]),
            ("参考文献", "references", [["文献．"]]),
        ]
    ).model_copy(update={"doc_id": doc_id})
    store.save_document(doc)
    aid = store.create_analysis(doc_id, kind="logic", settings=AnalysisSettings())
    box = BBox(page=1, x0=72, y0=110, x1=330, y1=123)
    store.save_findings(
        aid,
        [
            finding(
                "F-0001", "logic", box, "q", doc_id=doc_id, severity="critical", section_id="SEC-01"
            ),
            finding("F-0002", "readability", box, "q", doc_id=doc_id, section_id="SEC-03"),
            finding(
                "F-0003",
                "logic",
                box,
                "q",
                doc_id=doc_id,
                severity="suggestion",
                section_id="SEC-03",
            ),
        ],
    )
    store.finish_analysis(aid, status="completed")
    return aid


def test_summary_counts_and_previous(ctx: tuple[TestClient, Store, FakeQueue, bytes]) -> None:
    client, store, _, pdf = ctx
    doc_id = upload(client, pdf)
    first = seed(store, doc_id)
    second = seed(store, doc_id)
    store.set_finding_status(second, "F-0003", "dismissed", "意図どおり")
    s = client.get(f"/api/analyses/{second}").json()
    counts = s["counts"]
    assert counts["total"] == 2  # 却下は数えない
    assert counts["by_severity"] == {"critical": 1, "major": 1, "suggestion": 0}
    assert counts["by_category"]["logic"] == 1 and counts["by_category"]["readability"] == 1
    # 小見出しは大見出しにまとめ、論文の順に並べる。参考文献は含めない
    assert [(c["label"], c["count"]) for c in counts["by_section"]] == [
        ("1 はじめに", 1),
        ("2 手法", 1),
    ]
    assert counts["section_of"]["SEC-03"] == "2"  # 2.1 は 2 にまとめる
    assert s["previous"]["analysis_id"] == first and s["previous"]["total"] == 3
    assert s["models"]["logic_check"]
    assert client.get("/api/analyses/A-none").status_code == 404


def test_findings_filters_status_and_csv(ctx: tuple[TestClient, Store, FakeQueue, bytes]) -> None:
    client, store, _, pdf = ctx
    aid = seed(store, upload(client, pdf))
    base = f"/api/analyses/{aid}/findings"
    assert [f["finding_id"] for f in client.get(base, params={"category": "logic"}).json()] == [
        "F-0001",
        "F-0003",
    ]
    assert [f["finding_id"] for f in client.get(base, params={"section_id": "SEC-03"}).json()] == [
        "F-0002",
        "F-0003",
    ]
    assert client.get(base, params={"severity": "bogus"}).status_code == 422

    assert client.patch(f"{base}/F-0001", json={"status": "dismissed"}).status_code == 422
    r = client.patch(f"{base}/F-0001", json={"status": "dismissed", "reason": "意図した表現"})
    assert r.json()["status"] == "dismissed" and r.json()["dismiss_reason"] == "意図した表現"
    assert client.patch(f"{base}/F-9999", json={"status": "accepted"}).status_code == 404

    csv = client.get(f"/api/analyses/{aid}/findings.csv")
    text = csv.content.decode("utf-8")
    assert text.startswith("﻿番号,種類,内容,重大度")
    assert "F-0001,論理,主張の過大,重大" in text and "却下,意図した表現" in text


def test_single_judgement_has_no_confidence(
    ctx: tuple[TestClient, Store, FakeQueue, bytes],
) -> None:
    """多数決をしていない（1回だけの判定の）指摘は、確度で絞り込まれず、CSV でも確度を空欄にする。"""
    client, store, _, pdf = ctx
    doc_id = upload(client, pdf)
    doc = make_doc([("1 はじめに", "introduction", [["文．"]])])
    store.save_document(doc.model_copy(update={"doc_id": doc_id}))
    aid = store.create_analysis(doc_id, kind="logic", settings=AnalysisSettings())
    box = BBox(page=1, x0=72, y0=110, x1=330, y1=123)
    store.save_findings(
        aid,
        [
            finding("F-0001", "logic", box, "q", doc_id=doc_id, confidence="low", votes=2),
            finding(
                "F-0002", "citation", box, "q", doc_id=doc_id, confidence="low", votes=1, samples=1
            ),
        ],
    )
    store.finish_analysis(aid, status="completed")
    base = f"/api/analyses/{aid}/findings"
    low = client.get(base, params={"confidence": "low"}).json()
    assert [f["finding_id"] for f in low] == ["F-0001"]
    counts = client.get(f"/api/analyses/{aid}").json()["counts"]
    assert counts["by_confidence"] == {"low": 1}
    rows = client.get(f"{base}.csv").content.decode("utf-8").splitlines()
    assert rows[1].startswith("F-0001,論理,主張の過大,要修正,低,")
    assert rows[2].split(",")[4] == ""


def test_audit_log_when_gateway_unavailable(
    ctx: tuple[TestClient, Store, FakeQueue, bytes],
) -> None:
    client, *_ = ctx
    assert client.get("/api/audit-log").json() == {"available": False, "mode": None, "entries": []}


class RecordingGateway(GatewayClient):
    def __init__(self) -> None:
        super().__init__("http://gateway.test")
        self.decided: list[tuple[str, str]] = []

    async def mode(self) -> str:
        return "standard"

    async def audit(self, analysis_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        return [{"seq": 1, "decision": "sent", "kind": "bib_lookup", "analysis_id": analysis_id}]

    async def queue(self) -> list[dict[str, Any]]:
        return [{"request_id": "R-1", "kind": "term_lookup", "request": {"term": "BERT"}}]

    async def approve(self, request_id: str) -> GatewayResponse:
        self.decided.append((request_id, "approve"))
        return GatewayResponse(request_id=request_id, decision="sent")

    async def reject(self, request_id: str) -> GatewayResponse:
        self.decided.append((request_id, "reject"))
        return GatewayResponse(request_id=request_id, decision="rejected")

    async def external_count(self, analysis_id: str) -> int:
        return 7


def test_audit_log_and_queue_via_gateway(ctx: tuple[TestClient, Store, FakeQueue, bytes]) -> None:
    client, store, _, pdf = ctx
    gateway = RecordingGateway()
    app.dependency_overrides[get_gateway] = lambda: gateway
    log = client.get("/api/audit-log", params={"analysis_id": "A-1"}).json()
    assert (
        log["available"] and log["mode"] == "standard" and log["entries"][0]["analysis_id"] == "A-1"
    )
    assert client.get("/api/gateway/queue").json()[0]["request"]["term"] == "BERT"
    assert client.post("/api/gateway/queue/R-1/approve").json()["decision"] == "sent"
    assert client.post("/api/gateway/queue/R-1/reject").json()["decision"] == "rejected"
    assert client.post("/api/gateway/queue/R-1/delete").status_code == 422
    assert gateway.decided == [("R-1", "approve"), ("R-1", "reject")]
    aid = seed(store, upload(client, pdf))
    assert client.get(f"/api/analyses/{aid}").json()["external_requests"] == 7


def test_delete_document_removes_paper_and_analyses(
    ctx: tuple[TestClient, Store, FakeQueue, bytes],
) -> None:
    client, store, _, pdf = ctx
    doc_id = upload(client, pdf)
    aid = seed(store, doc_id)
    other = upload(client, pdf + b"\n% other")
    other_aid = seed(store, other)
    store.add_daily_usage(today_jst(), [_usage(3)])

    assert client.delete(f"/api/documents/{doc_id}").status_code == 204
    assert store.get_paper(doc_id) is None and store.get_document(doc_id) is None
    assert store.get_analysis(aid) is None and store.get_findings(aid) == []
    assert client.get(f"/api/documents/{doc_id}/paper.pdf").status_code == 404
    assert client.delete(f"/api/documents/{doc_id}").status_code == 404
    # ほかの論文と、その日の利用回数は残る
    assert store.get_analysis(other_aid) is not None
    assert client.get(f"/api/documents/{other}/paper.pdf").status_code == 200
    assert client.get("/api/usage/today").json()["requests"] == 3


def test_delete_document_refuses_while_analysis_runs(
    ctx: tuple[TestClient, Store, FakeQueue, bytes],
) -> None:
    client, store, _, pdf = ctx
    doc_id = upload(client, pdf)
    aid = store.create_analysis(doc_id, kind="logic", settings=AnalysisSettings(), status="queued")
    assert client.delete(f"/api/documents/{doc_id}").status_code == 409
    store.set_status(aid, "running")
    assert client.delete(f"/api/documents/{doc_id}").status_code == 409
    assert store.get_paper(doc_id) is not None
    assert client.get(f"/api/documents/{doc_id}/paper.pdf").status_code == 200


def _usage(requests: int, prompt: int = 100, completion: int = 50) -> UsageRow:
    return UsageRow(
        endpoint="chat/completions",
        model="m",
        role="logic_check",
        calls=requests,
        requests=requests,
        retries=0,
        failures=0,
        prompt_tokens=prompt,
        completion_tokens=completion,
        latency_s=1.0,
    )


def test_usage_today(ctx: tuple[TestClient, Store, FakeQueue, bytes]) -> None:
    client, store, *_ = ctx
    assert client.get("/api/usage/today").json() == {
        "day": today_jst(),
        "requests": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    store.add_daily_usage(today_jst(), [_usage(2), _usage(3, 10, 5)])
    store.add_daily_usage("2000-01-01", [_usage(99)])  # ほかの日は数えない
    body = client.get("/api/usage/today").json()
    assert (body["requests"], body["prompt_tokens"], body["completion_tokens"]) == (5, 110, 55)


def test_usage_unreported_returns_increments() -> None:
    from core.llm.usage import ApiUsage

    usage = ApiUsage()
    kw = {"endpoint": "chat/completions", "model": "m", "role": "r"}
    usage.record_request(**kw, attempt=1, status=200, latency_s=1.0)
    usage.record_tokens(**kw, prompt_tokens=10, completion_tokens=5)
    assert [(r.requests, r.prompt_tokens) for r in usage.unreported()] == [(1, 10)]
    assert usage.unreported() == []
    usage.record_request(**kw, attempt=2, status=200, latency_s=1.0)
    assert [(r.requests, r.prompt_tokens) for r in usage.unreported()] == [(1, 0)]


def test_diff_json_and_text_report(ctx: tuple[TestClient, Store, FakeQueue, bytes]) -> None:
    client, store, _, pdf = ctx
    doc_id = upload(client, pdf)
    # 初回は前回が無い
    first = seed(store, doc_id)
    assert client.get(f"/api/analyses/{first}/diff").json() == {
        "previous": None,
        "resolved": [],
        "new": [],
        "persisting": [],
    }
    second = seed(store, doc_id)
    box = BBox(page=1, x0=72, y0=110, x1=330, y1=123)
    # 2回目：F-0003 を直し（無くなった）、新しい指摘 F-0004 が出た
    store.save_findings(
        second,
        [
            finding(
                "F-0004", "logic", box, "新しく見つかった主張", doc_id=doc_id, subcategory="leap"
            ),
        ],
    )
    with store._connect() as conn:  # 2回目から F-0003 を除く（直した想定）
        conn.execute(
            "DELETE FROM findings WHERE analysis_id = ? AND finding_id = 'F-0003'", (second,)
        )
    diff = client.get(f"/api/analyses/{second}/diff").json()
    assert diff["previous"]["analysis_id"] == first
    assert diff["new"] == ["F-0004"]
    assert {p["finding_id"] for p in diff["persisting"]} == {"F-0001", "F-0002"}
    assert [f["finding_id"] for f in diff["resolved"]] == ["F-0003"]
    assert client.get("/api/analyses/A-none/diff").status_code == 404

    exported = client.get(f"/api/analyses/{second}/findings.json")
    assert "attachment" in exported.headers["content-disposition"]
    body = exported.json()
    assert body["analysis_id"] == second and body["paper"]["filename"] == "論文.pdf"
    assert {f["finding_id"] for f in body["findings"]} == {"F-0001", "F-0002", "F-0004"}

    report = client.get(f"/api/analyses/{second}/report.txt")
    assert report.headers["content-type"].startswith("text/plain")
    text = report.text
    assert "指摘は全部で3件" in text and "前回比 ±0件" in text
    assert "前回との差分：解消 1件、新規 1件、継続 2件。" in text
    assert "■ 主な指摘（重大・要修正、3件）" in text
    assert "■ 前回から解消された指摘（1件）" in text


def test_rule_documents_and_rules(ctx: tuple[TestClient, Store, FakeQueue, bytes]) -> None:
    client, _, queue, _ = ctx
    bad = client.post(
        "/api/rule-documents", files={"file": ("x.docx", b"x", "application/octet-stream")}
    )
    assert bad.status_code == 415
    r = client.post(
        "/api/rule-documents",
        files={"file": ("投稿規定.md", "要旨には目的を書く。".encode(), "text/markdown")},
    )
    assert r.status_code == 201
    doc = r.json()
    assert doc["kind"] == "markdown" and doc["status"] == "queued"
    assert queue.enqueued == [doc["document_id"]]
    assert [d["document_id"] for d in client.get("/api/rule-documents").json()] == [
        doc["document_id"]
    ]
    # 抽出中は削除できない
    assert client.delete(f"/api/rule-documents/{doc['document_id']}").status_code == 409

    rules = client.get("/api/rules").json()
    assert {x["rule_id"] for x in rules} >= {"B-ABBR", "B-FIGREF", "B-UNIT"}
    off = client.patch("/api/rules/B-UNIT", json={"status": "disabled"}).json()
    assert off["status"] == "disabled"
    assert client.patch("/api/rules/R-none", json={"status": "approved"}).status_code == 404
    assert client.patch("/api/rules/B-UNIT", json={"status": "bogus"}).status_code == 422
    bulk = client.post(
        "/api/rules/bulk-status",
        json={"rule_ids": ["B-UNIT", "B-ABBR", "R-none"], "status": "approved"},
    ).json()
    assert [x["rule_id"] for x in bulk] == ["B-UNIT", "B-ABBR"]
    assert {
        x["rule_id"] for x in client.get("/api/rules", params={"status": "disabled"}).json()
    } == set()


def test_lab_documents_and_comments(
    ctx: tuple[TestClient, Store, FakeQueue, bytes], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, queue, pdf = ctx
    monkeypatch.setenv("MONGAI_LAB_ID", "lab-a")
    assert (
        client.post(
            "/api/lab/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")}
        ).status_code
        == 415
    )
    # 拡張子と中身が合わない（.docx なのに ZIP でない）
    assert (
        client.post(
            "/api/lab/documents", files={"file": ("x.docx", b"text", "application/octet-stream")}
        ).status_code
        == 415
    )
    r = client.post("/api/lab/documents", files={"file": ("2024査読.pdf", pdf, "application/pdf")})
    assert r.status_code == 201
    doc = r.json()
    assert doc["lab_id"] == "lab-a" and queue.enqueued == [doc["document_id"]]
    assert client.delete(f"/api/lab/documents/{doc['document_id']}").status_code == 409

    lab = LabStore(tmp_path / "db.sqlite3")
    lab.add_comments(
        [
            LabComment(
                comment_id="LC-1",
                lab_id="lab-a",
                document_id=doc["document_id"],
                comment="一般化しすぎ",
                source=LabSource(doc="2024査読.pdf"),
            ),
            LabComment(
                comment_id="LC-9",
                lab_id="lab-b",
                document_id="LD-000000000009",
                comment="ほかの研究室",
                source=LabSource(doc="x.pdf"),
            ),
        ],
        [None, None],
    )
    assert [c["comment_id"] for c in client.get("/api/lab/comments").json()] == ["LC-1"]
    assert (
        client.delete("/api/lab/comments/LC-9").status_code == 404
    )  # ほかの研究室のものは消せない
    assert client.delete("/api/lab/comments/LC-1").status_code == 204
    lab.set_document_status(doc["document_id"], "completed")
    assert client.delete(f"/api/lab/documents/{doc['document_id']}").status_code == 204
    assert client.get("/api/lab/documents").json() == []


def test_sample_analysis_is_listed_and_cannot_be_deleted(
    ctx: tuple[TestClient, Store, FakeQueue, bytes],
) -> None:
    client, store, _, pdf = ctx
    assert install_sample(store)
    assert not install_sample(store)  # 2回目は取り込まない
    other = upload(client, pdf)
    items = {i["doc_id"]: i for i in client.get("/api/analyses").json()}
    sample = items[SAMPLE_DOC_ID]
    assert sample["analysis_id"] == SAMPLE_ANALYSIS_ID and sample["sample"] is True
    assert sample["total"] == 13 and sample["title"]
    summary = client.get(f"/api/analyses/{SAMPLE_ANALYSIS_ID}").json()
    assert summary["sample"] is True and summary["counts"]["total"] == 13
    assert client.get(f"/api/documents/{SAMPLE_DOC_ID}/paper.pdf").status_code == 200
    assert client.get(f"/api/analyses/{SAMPLE_ANALYSIS_ID}/annotated.pdf").status_code == 200

    r = client.delete(f"/api/documents/{SAMPLE_DOC_ID}")
    assert r.status_code == 403
    assert store.get_analysis(SAMPLE_ANALYSIS_ID) is not None
    assert client.delete(f"/api/documents/{other}").status_code == 204  # ほかの論文は削除できる


class TermGateway(RecordingGateway):
    """承認すると、用語の説明（学外の辞書の結果）を返す gateway。"""

    async def approve(self, request_id: str) -> GatewayResponse:
        self.decided.append((request_id, "approve"))
        term = TermResult(term="Isochrone", description="等時間で到達できる範囲", source="openalex")
        return GatewayResponse(
            request_id=request_id, decision="sent", response=SearchResponse(term=term)
        )


def test_term_lookups_follow_queue_decisions(
    ctx: tuple[TestClient, Store, FakeQueue, bytes],
) -> None:
    client, store, _, pdf = ctx
    app.dependency_overrides[get_gateway] = lambda: TermGateway()
    aid = seed(store, upload(client, pdf))
    store.save_term_lookups(
        aid,
        [
            TermLookupRecord(term="アイソクローン", request_id="R-1", status="pending"),
            TermLookupRecord(term="通院", request_id="R-2", status="pending"),
            TermLookupRecord(term="DRT-Opt", request_id="R-3", status="blocked", reason="造語"),
        ],
    )
    assert client.post("/api/gateway/queue/R-1/approve").status_code == 200
    assert client.post("/api/gateway/queue/R-2/reject").status_code == 200
    terms = {t["term"]: t for t in client.get(f"/api/analyses/{aid}/terms").json()}
    assert terms["アイソクローン"]["status"] == "approved"
    assert terms["アイソクローン"]["description"] == "等時間で到達できる範囲"
    assert terms["アイソクローン"]["source"] == "openalex"
    assert terms["アイソクローン"]["matched"] == "Isochrone"
    assert terms["通院"]["status"] == "rejected" and terms["通院"]["description"] is None
    assert terms["DRT-Opt"]["status"] == "blocked" and terms["DRT-Opt"]["reason"] == "造語"
    assert client.get("/api/analyses/A-none/terms").status_code == 404
    # 論文を削除すると、照会の記録も消える
    assert client.delete(f"/api/documents/{store.get_analysis(aid).doc_id}").status_code == 204  # type: ignore[union-attr]
    assert store.get_term_lookups(aid) == []
