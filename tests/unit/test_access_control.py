"""アクセス制御（docs/access-control-design.md 第J章）：ゲスト・利用者 A・利用者 B・共通デモ。

主体は get_principal を差し替えて切り替える。データはすべて合成データ。
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.analyze.pipeline import AnalysisSettings
from core.api.main import (
    app,
    get_gateway,
    get_lab_store,
    get_principal,
    get_queue,
    get_rule_store,
    get_store,
)
from core.auth.principal import AuthenticatedUser, Guest, Principal
from core.ingest.models import BBox
from core.lab.models import LabComment, LabSource
from core.lab.store import LabStore
from core.rules.store import RuleStore
from core.sample import SAMPLE_ANALYSIS_ID, SAMPLE_DIR, SAMPLE_DOC_ID, install_sample
from core.store import Store
from shared.schemas import GatewayResponse
from tests.unit.auth_factory import make_user
from tests.unit.doc_factory import make_doc
from tests.unit.pdf_factory import Text, make_pdf
from tests.unit.test_annotate import finding
from tests.unit.test_api import FakeQueue, RecordingGateway

DEMO_READS = [
    f"/api/analyses/{SAMPLE_ANALYSIS_ID}",
    f"/api/analyses/{SAMPLE_ANALYSIS_ID}/findings",
    f"/api/analyses/{SAMPLE_ANALYSIS_ID}/terms",
    f"/api/analyses/{SAMPLE_ANALYSIS_ID}/diff",
    f"/api/analyses/{SAMPLE_ANALYSIS_ID}/findings.csv",
    f"/api/analyses/{SAMPLE_ANALYSIS_ID}/findings.json",
    f"/api/analyses/{SAMPLE_ANALYSIS_ID}/report.txt",
    f"/api/analyses/{SAMPLE_ANALYSIS_ID}/annotated.pdf",
    f"/api/documents/{SAMPLE_DOC_ID}/paper.pdf",
]


class GuardedGateway(RecordingGateway):
    """学外への送信（submit・approve・reject）が呼ばれたら記録する gateway。"""

    def __init__(self, pending: dict[str, str] | None = None) -> None:
        super().__init__(pending if pending is not None else {})
        self.submitted: list[Any] = []

    async def submit(self, analysis_id: str, doc_id: str, request: Any) -> GatewayResponse:
        self.submitted.append(request)
        return GatewayResponse(request_id="R-x", decision="blocked")


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.store = Store(tmp_path / "db.sqlite3")
        self.rules = RuleStore(self.store.path)
        self.lab = LabStore(self.store.path)
        self.queue = FakeQueue()
        self.gateway = GuardedGateway()
        self.a = make_user(self.store.path, "alice")
        self.b = make_user(self.store.path, "bob")
        self.pdf = make_pdf(
            tmp_path / "p.pdf", [[Text(72, 120, "A synthetic paper.")]]
        ).read_bytes()
        install_sample(self.store)
        app.dependency_overrides[get_store] = lambda: self.store
        app.dependency_overrides[get_rule_store] = lambda: self.rules
        app.dependency_overrides[get_lab_store] = lambda: self.lab
        app.dependency_overrides[get_queue] = lambda: self.queue
        app.dependency_overrides[get_gateway] = lambda: self.gateway
        self.client = TestClient(app)
        self.as_guest()

    def act(self, principal: Principal) -> TestClient:
        app.dependency_overrides[get_principal] = lambda: principal
        return self.client

    def as_guest(self) -> TestClient:
        return self.act(Guest())

    def as_a(self) -> TestClient:
        return self.act(AuthenticatedUser(self.a))

    def as_b(self) -> TestClient:
        return self.act(AuthenticatedUser(self.b))

    def upload(self, client: TestClient, pdf: bytes | None = None, name: str = "paper.pdf") -> str:
        r = client.post(
            "/api/documents", files={"file": (name, pdf or self.pdf, "application/pdf")}
        )
        assert r.status_code == 201, r.text
        return str(r.json()["doc_id"])

    def analysis(self, doc_id: str, owner: str, title: str = "合成論文") -> str:
        """完了した解析（指摘2件）を作る。"""
        doc = make_doc([("1 はじめに", "introduction", [["文．"]])]).model_copy(
            update={"doc_id": doc_id, "title": title}
        )
        self.store.save_document(doc)
        aid = self.store.create_analysis(
            doc_id, owner_user_id=owner, kind="logic", settings=AnalysisSettings()
        )
        box = BBox(page=1, x0=72, y0=110, x1=330, y1=123)
        self.store.save_findings(
            aid,
            [
                finding("F-0001", "logic", box, "q", doc_id=doc_id),
                finding("F-0002", "readability", box, "q", doc_id=doc_id),
            ],
        )
        self.store.finish_analysis(aid, status="completed")
        return aid


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    yield Env(tmp_path)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- ゲスト


def test_guest_sees_only_the_demo(env: Env) -> None:
    """J.2 #1・#2：ゲストの一覧は共通デモだけ。利用者の解析は件数・ID・ファイル名も出ない。"""
    a_doc = env.upload(env.as_a(), name="secret-paper.pdf")
    a_aid = env.analysis(a_doc, env.a)
    items = env.as_guest().get("/api/analyses").json()
    assert [(i["analysis_id"], i["sample"]) for i in items] == [(SAMPLE_ANALYSIS_ID, True)]
    assert a_aid not in str(items) and "secret-paper" not in str(items)


def test_guest_can_read_every_demo_view_without_side_effects(env: Env) -> None:
    """J.2 #3・#23：デモの読み取りはすべて 200。ジョブ・学外への送信・利用回数は増えない。"""
    client = env.as_guest()
    for path in DEMO_READS:
        assert client.get(path).status_code == 200, path
    pdf = client.get(f"/api/documents/{SAMPLE_DOC_ID}/paper.pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content == (SAMPLE_DIR / "paper.pdf").read_bytes()
    assert client.get(f"/api/audit-log?analysis_id={SAMPLE_ANALYSIS_ID}").status_code == 200
    assert env.queue.enqueued == [] and env.gateway.submitted == [] and env.gateway.decided == []
    with env.store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM api_usage_daily").fetchone()[0] == 0
    # デモの外部通信ログは、利用者を指定せずにデモの解析 ID で問い合わせる
    assert env.gateway.audited == [(SAMPLE_ANALYSIS_ID, None)]


def test_guest_cannot_read_personal_data(env: Env) -> None:
    """J.2 #4：ゲストが他人の PDF・解析を ID で指定しても 404（存在しない ID と区別できない）。"""
    a_doc = env.upload(env.as_a())
    a_aid = env.analysis(a_doc, env.a)
    client = env.as_guest()
    mine = client.get(f"/api/documents/{a_doc}/paper.pdf")
    unknown = client.get("/api/documents/0123456789abcdef/paper.pdf")
    assert mine.status_code == unknown.status_code == 404
    assert mine.json() == unknown.json()
    for suffix in ("", "/findings", "/terms", "/diff", "/findings.csv", "/report.txt"):
        assert client.get(f"/api/analyses/{a_aid}{suffix}").status_code == 404, suffix


def test_guest_cannot_write_or_use_personal_features(env: Env) -> None:
    """J.2 #5〜#7・#24・#27：ゲストの書き込み・個人の機能は 401。何も作られず、何も積まれない。"""
    client = env.as_guest()
    r = client.post("/api/documents", files={"file": ("p.pdf", env.pdf, "application/pdf")})
    assert r.status_code == 401 and r.json()["detail"] == "login_required"
    a_doc = env.upload(env.as_a())
    client = env.as_guest()
    for doc_id in (SAMPLE_DOC_ID, a_doc):
        assert client.post("/api/analyses", json={"doc_id": doc_id}).status_code == 401
    assert env.queue.enqueued == []
    before = [f.status for f in env.store.get_findings(SAMPLE_ANALYSIS_ID)]
    finding_id = env.store.get_findings(SAMPLE_ANALYSIS_ID)[0].finding_id
    patch = f"/api/analyses/{SAMPLE_ANALYSIS_ID}/findings/{finding_id}"
    assert client.patch(patch, json={"status": "accepted"}).status_code == 401
    assert [f.status for f in env.store.get_findings(SAMPLE_ANALYSIS_ID)] == before
    assert client.delete(f"/api/documents/{SAMPLE_DOC_ID}").status_code == 401
    for path in (
        "/api/usage/today",
        "/api/rules",
        "/api/rule-documents",
        "/api/lab/documents",
        "/api/lab/comments",
        "/api/gateway/queue",
        "/api/audit-log",
        "/api/audit-log?analysis_id=A-000000000000",
    ):
        assert client.get(path).status_code == 401, path
    assert env.gateway.audited == []  # ゲストの要求で gateway に問い合わせない
    with env.store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 2  # デモと A の論文


# ---------------------------------------------------------------- 共通デモは誰も変えられない


def test_users_cannot_change_the_demo(env: Env) -> None:
    """J.2 #8：利用者でも、デモの指摘の変更・論文の削除・再解析は 403。"""
    client = env.as_a()
    finding_id = env.store.get_findings(SAMPLE_ANALYSIS_ID)[0].finding_id
    patch = f"/api/analyses/{SAMPLE_ANALYSIS_ID}/findings/{finding_id}"
    assert client.patch(patch, json={"status": "accepted"}).status_code == 403
    assert client.delete(f"/api/documents/{SAMPLE_DOC_ID}").status_code == 403
    assert client.post("/api/analyses", json={"doc_id": SAMPLE_DOC_ID}).status_code == 403
    assert env.queue.enqueued == []
    record = env.store.get_analysis(SAMPLE_ANALYSIS_ID)
    assert record is not None and record.status == "completed"
    assert {f.status for f in env.store.get_findings(SAMPLE_ANALYSIS_ID)} == {"open"}
    # 利用者の一覧には、自分の解析と一緒にデモも出る
    assert [i["analysis_id"] for i in client.get("/api/analyses").json()] == [SAMPLE_ANALYSIS_ID]


# ---------------------------------------------------------------- 利用者どうしの分離


def test_owner_can_use_own_data_and_others_cannot(env: Env) -> None:
    """J.2 #9〜#12：A は自分のデータを使える。B は A の論文・解析・エクスポートに 404。"""
    a_doc = env.upload(env.as_a())
    a_aid = env.analysis(a_doc, env.a)
    client = env.as_a()
    reads = [
        f"/api/documents/{a_doc}/paper.pdf",
        f"/api/analyses/{a_aid}",
        f"/api/analyses/{a_aid}/findings",
        f"/api/analyses/{a_aid}/terms",
        f"/api/analyses/{a_aid}/diff",
        f"/api/analyses/{a_aid}/findings.csv",
        f"/api/analyses/{a_aid}/findings.json",
        f"/api/analyses/{a_aid}/report.txt",
        f"/api/analyses/{a_aid}/annotated.pdf",
    ]
    for path in reads:
        assert client.get(path).status_code == 200, path
    client = env.as_b()
    for path in reads:
        assert client.get(path).status_code == 404, path
    assert client.get("/api/analyses").json()[0]["analysis_id"] == SAMPLE_ANALYSIS_ID
    assert len(client.get("/api/analyses").json()) == 1
    # B は A のデータを変えられない
    patch = f"/api/analyses/{a_aid}/findings/F-0001"
    assert client.patch(patch, json={"status": "accepted"}).status_code == 404
    assert client.delete(f"/api/documents/{a_doc}").status_code == 404
    assert client.post("/api/analyses", json={"doc_id": a_doc}).status_code == 404
    assert client.get(f"/api/audit-log?analysis_id={a_aid}").status_code == 404
    assert env.store.get_paper(a_doc) is not None
    assert env.store.get_findings(a_aid)[0].status == "open"
    assert env.queue.enqueued == []


def test_rules_and_lab_knowledge_are_separated(env: Env) -> None:
    """J.2 #12〜#15：ルール・ルール資料・専門知識は本人の分だけ。組み込みのルールの設定も本人だけ。"""
    a = env.as_a()
    doc = a.post(
        "/api/rule-documents",
        files={"file": ("A学会の投稿規定.md", "要旨には目的を書く。".encode(), "text/markdown")},
    ).json()
    assert a.patch("/api/rules/B-UNIT", json={"status": "disabled"}).json()["status"] == "disabled"
    from tests.unit.test_rules import rule

    env.rules.add_rules(env.a, [rule("R-a", "A のルール", document_id=doc["document_id"])], [None])
    env.lab.add_comments(
        env.a,
        [
            LabComment(
                comment_id="LC-a",
                document_id="LD-000000000001",
                comment="A の指摘",
                source=LabSource(doc="a.md"),
            )
        ],
        [None],
    )
    b = env.as_b()
    assert b.get("/api/rule-documents").json() == []
    assert b.get("/api/lab/comments").json() == []
    b_rules = {r["rule_id"]: r for r in b.get("/api/rules").json()}
    assert "R-a" not in b_rules and b_rules["B-UNIT"]["status"] == "approved"
    assert "A学会" not in str(b_rules)
    assert b.patch("/api/rules/R-a", json={"status": "approved"}).status_code == 404
    bulk = b.post(
        "/api/rules/bulk-status", json={"rule_ids": ["R-a", "B-ABBR"], "status": "disabled"}
    )
    assert [r["rule_id"] for r in bulk.json()] == ["B-ABBR"]
    assert b.delete(f"/api/rule-documents/{doc['document_id']}").status_code == 404
    assert b.delete("/api/lab/comments/LC-a").status_code == 404
    # A のデータは変わらない
    a_rules = {r.rule_id: r for r in env.rules.list_rules(env.a)}
    assert a_rules["R-a"].status != "disabled" and a_rules["B-ABBR"].status == "approved"
    assert [c.comment_id for c in env.lab.list_comments(env.a)] == ["LC-a"]


def test_previous_analysis_is_never_someone_elses(env: Env) -> None:
    """J.2 #18・#19：前回の解析は同じ所有者の解析だけ。デモにも個人の解析を前回として出さない。"""
    a_doc = env.upload(env.as_a(), name="paper.pdf")
    env.analysis(a_doc, env.a, title="Demo title")
    b_doc = env.upload(env.as_b(), name="paper.pdf")
    b_aid = env.analysis(b_doc, env.b, title="Demo title")
    b = env.as_b()
    assert b.get(f"/api/analyses/{b_aid}").json()["previous"] is None
    assert b.get(f"/api/analyses/{b_aid}/diff").json()["previous"] is None
    assert "前回比" not in b.get(f"/api/analyses/{b_aid}/report.txt").text
    # 2回目の B の解析では、前回は B の1回目
    b_second = env.analysis(b_doc, env.b, title="Demo title")
    assert b.get(f"/api/analyses/{b_second}").json()["previous"]["analysis_id"] == b_aid
    # デモの解析と同じタイトルの個人の解析があっても、デモの前回にはしない
    demo_title = env.store.get_document(SAMPLE_DOC_ID).title  # type: ignore[union-attr]
    env.analysis(a_doc, env.a, title=demo_title or "")
    guest = env.as_guest()
    assert guest.get(f"/api/analyses/{SAMPLE_ANALYSIS_ID}").json()["previous"] is None
    assert guest.get(f"/api/analyses/{SAMPLE_ANALYSIS_ID}/diff").json()["previous"] is None


def test_same_pdf_uploaded_by_two_users(env: Env) -> None:
    """J.2 #20・#21：同じ PDF でも利用者・アップロードごとに別の論文。削除しても相手に影響しない。"""
    a_doc = env.upload(env.as_a())
    b_doc = env.upload(env.as_b())
    assert a_doc != b_doc
    b_aid = env.analysis(b_doc, env.b)
    assert env.as_a().delete(f"/api/documents/{a_doc}").status_code == 204
    b = env.as_b()
    assert b.get(f"/api/documents/{b_doc}/paper.pdf").content == env.pdf
    assert b.get(f"/api/analyses/{b_aid}").status_code == 200
    # デモと同じ PDF も、デモとは別の個人の論文になる
    demo_pdf = (SAMPLE_DIR / "paper.pdf").read_bytes()
    demo_before = env.store.get_paper(SAMPLE_DOC_ID)
    copied = env.upload(b, demo_pdf, name="demo_paper_ja.pdf")
    assert copied != SAMPLE_DOC_ID
    assert env.store.get_paper(SAMPLE_DOC_ID) == demo_before
    assert env.store.get_paper(copied).content_sha256 == demo_before.content_sha256  # type: ignore[union-attr]


def test_usage_today_is_per_user(env: Env) -> None:
    """J.2 #27：今日の利用回数は本人の分だけ。"""
    from core.store import today_jst
    from tests.unit.test_api import _usage

    env.store.add_daily_usage(today_jst(), [_usage(2)], user_id=env.a)
    env.store.add_daily_usage(today_jst(), [_usage(5)], user_id=env.b)
    assert env.as_a().get("/api/usage/today").json()["requests"] == 2
    assert env.as_b().get("/api/usage/today").json()["requests"] == 5


def test_gateway_queue_is_per_user(env: Env) -> None:
    """J.2 #25：B には A の承認待ちが見えず、承認もできない（gateway に承認が送られない）。"""
    env.gateway.pending = {"R-a": env.a}
    assert [q["request_id"] for q in env.as_a().get("/api/gateway/queue").json()] == ["R-a"]
    b = env.as_b()
    assert b.get("/api/gateway/queue").json() == []
    assert b.post("/api/gateway/queue/R-a/approve").status_code == 404
    assert b.post("/api/gateway/queue/R-a/reject").status_code == 404
    assert env.gateway.decided == []
    assert env.as_a().post("/api/gateway/queue/R-a/approve").status_code == 200
    assert env.gateway.decided == [("R-a", "approve")]


def test_demo_terms_ignore_queue_decisions(env: Env) -> None:
    """共通デモの用語の照会は保存済みの状態のまま（承認キューの決定を重ねない）。"""
    before = env.as_guest().get(f"/api/analyses/{SAMPLE_ANALYSIS_ID}/terms").json()
    for term in env.store.get_term_lookups(SAMPLE_ANALYSIS_ID):
        if term.request_id:
            env.store.decide_term_lookup(term.request_id, "rejected", owner_user_id=env.a)
    assert env.as_guest().get(f"/api/analyses/{SAMPLE_ANALYSIS_ID}/terms").json() == before


def test_cross_site_writes_are_rejected(env: Env) -> None:
    """ほかのサイトから送られた書き込み（CSRF）は 403。同じサイトからの要求は通る。"""
    client = env.as_a()
    files = {"file": ("p.pdf", env.pdf, "application/pdf")}
    assert (
        client.post(
            "/api/documents", files=files, headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/documents", files=files, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    ok = client.post(
        "/api/documents",
        files=files,
        headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"},
    )
    assert ok.status_code == 201


def test_replaced_demo_is_removed(env: Env) -> None:
    """デモを差し替えたら、前のデモ（公開データ）は消え、全員に見えるデモは新しいものだけになる。"""
    from core.store.files import load_paper, save_paper

    old = env.store.get_analysis(SAMPLE_ANALYSIS_ID)
    assert old is not None
    old_doc, old_aid = "0123456789abcdef", "A-0123456789ab"
    save_paper(b"%PDF-1.4 old demo", old_doc)
    env.store.save_paper(
        old_doc, owner_user_id=None, filename="old.pdf", size=1, visibility="public_demo"
    )
    env.store.import_analysis(
        old.model_copy(update={"analysis_id": old_aid, "doc_id": old_doc}), [], []
    )
    a_doc = env.upload(env.as_a())  # 利用者のデータは消さない
    assert len(env.as_guest().get("/api/analyses").json()) == 2
    assert not install_sample(env.store)  # 新しいデモは取り込み済み。前のデモだけを消す
    assert [i["analysis_id"] for i in env.as_guest().get("/api/analyses").json()] == [
        SAMPLE_ANALYSIS_ID
    ]
    assert env.store.get_paper(old_doc) is None and load_paper(old_doc) is None
    assert env.store.get_paper(a_doc) is not None and env.store.get_paper(SAMPLE_DOC_ID) is not None
