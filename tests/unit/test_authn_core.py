"""認証サービス（authn）と core の結合：偽の Google でログインし、nginx と同じ手順で主張を core に渡す。

nginx（docker/web/auth/authn/）の役割は Web クラスで再現する：/api/ の要求ごとに authn の /auth/verify に
問い合わせ、401 なら 401、200 なら応答の主張だけを core に渡す（ブラウザから来た同名のヘッダーは上書きする）。
nginx の設定そのものは tests/security/run.sh で、実際のコンテナに対して確かめる。
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from authn.main import create_app
from core.api.main import app as core_app
from core.api.main import get_queue, get_store, get_user_directory, get_verifier
from core.auth.assertion import ASSERTION_HEADER, AssertionVerifier
from core.auth.directory import UserDirectory
from core.sample import SAMPLE_ANALYSIS_ID, SAMPLE_DOC_ID, install_sample
from core.store import Store
from core.store.users import UserStore
from tests.unit.fake_google import FakeGoogle
from tests.unit.pdf_factory import Text, make_pdf
from tests.unit.test_authn import ORIGIN, make_settings


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[str] = []

    async def enqueue(self, analysis_id: str) -> None:
        self.jobs.append(analysis_id)

    async def enqueue_rules(self, document_id: str) -> None:
        self.jobs.append(document_id)

    async def enqueue_lab(self, document_id: str) -> None:
        self.jobs.append(document_id)


class Web:
    """ブラウザ1つ分（Cookie を持つ）と、nginx の auth_request の再現。"""

    def __init__(self, authn: TestClient, core: TestClient, google: FakeGoogle) -> None:
        self.authn = authn
        self.core = core
        self.google = google

    def login(self, **claims: Any) -> httpx.Response:
        r = self.authn.get("/auth/login", follow_redirects=False)
        code, state = self.google.authorize(r.headers["location"], **claims)
        callback: httpx.Response = self.authn.get(
            "/auth/callback", params={"state": state, "code": code}, follow_redirects=False
        )
        return callback

    def logout(self) -> httpx.Response:
        response: httpx.Response = self.authn.post(
            "/auth/logout", headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
        )
        return response

    def api(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        verify = self.authn.get("/auth/verify")
        if verify.status_code == 401:
            return httpx.Response(401, json={"detail": "invalid_session"})
        if verify.status_code != 200:
            return httpx.Response(503, json={"detail": "auth_unavailable"})
        headers = dict(kwargs.pop("headers", {}))
        headers.pop(ASSERTION_HEADER, None)  # ブラウザから来た主張は捨てる
        token = verify.headers.get(ASSERTION_HEADER)
        if token:
            headers[ASSERTION_HEADER] = token
        response: httpx.Response = self.core.request(method, path, headers=headers, **kwargs)
        return response


class Env:
    def __init__(self, tmp_path: Path, google: FakeGoogle) -> None:
        self.settings = make_settings(tmp_path)
        self.authn_app = create_app(self.settings, google.transport())
        self.store = Store(tmp_path / "db.sqlite3")
        install_sample(self.store)
        self.queue = FakeQueue()
        self.google = google
        self.pdf = make_pdf(
            tmp_path / "p.pdf", [[Text(72, 120, "A synthetic paper.")]]
        ).read_bytes()

    def browser(self) -> Web:
        authn = TestClient(self.authn_app, base_url=ORIGIN)
        return Web(authn, TestClient(core_app, base_url=ORIGIN), self.google)

    def users(self) -> int:
        with sqlite3.connect(self.store.path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def papers(self) -> int:
        with sqlite3.connect(self.store.path) as conn:
            query = "SELECT COUNT(*) FROM papers WHERE visibility = 'private'"
            return int(conn.execute(query).fetchone()[0])


@pytest.fixture
def env(tmp_path: Path, google_: FakeGoogle, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("MONGAI_ENV", "production")
    monkeypatch.setenv("MONGAI_ALLOWED_EMAIL_DOMAINS", "okayama-u.ac.jp")
    e = Env(tmp_path, google_)
    with TestClient(e.authn_app, base_url=ORIGIN):  # 起動（鍵を作る）
        verifier = AssertionVerifier(e.settings.public_key_file)
        core_app.dependency_overrides[get_store] = lambda: e.store
        core_app.dependency_overrides[get_verifier] = lambda: verifier
        core_app.dependency_overrides[get_user_directory] = lambda: UserDirectory(
            UserStore(e.store.path)
        )
        core_app.dependency_overrides[get_queue] = lambda: e.queue
        yield e
    core_app.dependency_overrides.clear()


@pytest.fixture
def google_() -> FakeGoogle:
    return FakeGoogle()


ALICE = {
    "sub": "200000000000000000001",
    "email": "alice@s.okayama-u.ac.jp",
    "hd": "okayama-u.ac.jp",
}
BOB = {"sub": "200000000000000000002", "email": "bob@okayama-u.ac.jp", "hd": "okayama-u.ac.jp"}


def upload(web: Web, pdf: bytes) -> httpx.Response:
    return web.api(
        "POST",
        "/api/documents",
        files={"file": ("paper.pdf", pdf, "application/pdf")},
        headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"},
    )


def test_guest_can_browse_the_demo_but_not_use_personal_features(env: Env) -> None:
    guest = env.browser()
    assert guest.api("GET", "/api/me").json() == {
        "authenticated": False,
        "email": None,
        "display_name": None,
    }
    items = guest.api("GET", "/api/analyses").json()
    assert [(i["analysis_id"], i["sample"]) for i in items] == [(SAMPLE_ANALYSIS_ID, True)]
    assert guest.api("GET", f"/api/documents/{SAMPLE_DOC_ID}/paper.pdf").status_code == 200
    r = upload(guest, env.pdf)
    assert r.status_code == 401 and r.json()["detail"] == "login_required"
    assert env.papers() == 0 and env.queue.jobs == []


def test_login_use_own_data_and_logout(env: Env) -> None:
    alice = env.browser()
    assert alice.login(**ALICE).headers["location"] == "/"
    me = alice.api("GET", "/api/me").json()
    assert me["authenticated"] is True and me["email"] == "alice@s.okayama-u.ac.jp"
    assert "user_id" not in me and "subject" not in me  # 内部・外部の識別子は返さない
    doc_id = upload(alice, env.pdf).json()["doc_id"]
    assert alice.api("GET", f"/api/documents/{doc_id}/paper.pdf").status_code == 200
    stolen = alice.authn.cookies["mongai_session"]

    assert alice.logout().status_code == 204
    assert alice.api("GET", "/api/me").json()["authenticated"] is False
    assert alice.api("GET", f"/api/documents/{doc_id}/paper.pdf").status_code == 404
    # ログアウトしたセッションの Cookie は、ゲストにもならず 401（盗まれても使えない）
    alice.authn.cookies.set("mongai_session", stolen)
    r = alice.api("GET", f"/api/documents/{doc_id}/paper.pdf")
    assert r.status_code == 401 and r.json()["detail"] == "invalid_session"
    assert upload(alice, env.pdf).status_code == 401
    assert env.papers() == 1  # 認証エラーのときにデータは増えず、誰にも紐づかない

    # ログインし直すと、同じ利用者として自分のデータに戻れる
    alice.authn.cookies.clear()
    alice.login(**ALICE)
    assert alice.api("GET", f"/api/documents/{doc_id}/paper.pdf").status_code == 200


def test_users_cannot_access_each_others_data(env: Env) -> None:
    alice, bob = env.browser(), env.browser()
    alice.login(**ALICE)
    bob.login(**BOB)
    doc_id = upload(alice, env.pdf).json()["doc_id"]
    same = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
    assert bob.api("GET", f"/api/documents/{doc_id}/paper.pdf").status_code == 404
    assert bob.api("DELETE", f"/api/documents/{doc_id}", headers=same).status_code == 404
    assert alice.api("GET", f"/api/documents/{doc_id}/paper.pdf").status_code == 200
    # デモは誰も変更・再解析できない
    r = alice.api("POST", "/api/analyses", json={"doc_id": SAMPLE_DOC_ID}, headers=same)
    assert r.status_code == 403 and env.queue.jobs == []
    r = alice.api("DELETE", f"/api/documents/{SAMPLE_DOC_ID}", headers=same)
    assert r.status_code in (403, 404)
    assert env.browser().api("GET", f"/api/documents/{SAMPLE_DOC_ID}/paper.pdf").status_code == 200


def test_same_email_with_another_google_account_is_another_user(env: Env) -> None:
    """同じメールアドレスでも、Google の sub が違えば別の利用者（自動で統合しない）。"""
    alice = env.browser()
    alice.login(**ALICE)
    doc_id = upload(alice, env.pdf).json()["doc_id"]
    other = env.browser()
    other.login(**{**ALICE, "sub": "299999999999999999999"})
    assert other.api("GET", "/api/me").json()["authenticated"] is True
    assert other.api("GET", f"/api/documents/{doc_id}/paper.pdf").status_code == 404
    assert env.users() == 2


def test_rejected_logins_create_no_users(env: Env) -> None:
    for claims in (
        {"email": "eve@gmail.com", "hd": None},
        {"email": "eve@s.okayama-u.ac.jp", "hd": None},
        {"email": "eve@s.okayama-u.ac.jp", "email_verified": False},
        {"aud": "another-client"},
    ):
        web = env.browser()
        assert "auth_error=" in web.login(**claims).headers["location"]
        assert web.api("GET", "/api/me").json()["authenticated"] is False
    assert env.users() == 0


def test_forged_assertions_are_never_accepted(env: Env) -> None:
    guest = env.browser()
    forged = "eyJhbGciOiJFZERTQSIsImtpZCI6ImF1dGhuLXgifQ.e30.AAAA"
    # nginx が上書きするので、ブラウザから送った主張は core に届かない（ゲストのまま）
    r = guest.api("GET", "/api/me", headers={ASSERTION_HEADER: forged})
    assert r.json()["authenticated"] is False
    # 仮に届いても、署名を確かめるので 401（ゲストにもならない）
    r = guest.core.get("/api/me", headers={ASSERTION_HEADER: forged})
    assert r.status_code == 401 and r.json()["detail"] == "invalid_session"


def test_auth_store_failure_does_not_fall_back_to_guest(env: Env) -> None:
    alice = env.browser()
    alice.login(**ALICE)
    runtime = env.authn_app.state.runtime

    def broken(*_: Any, **__: Any) -> Any:
        from authn.store import StoreError

        raise StoreError("偽の障害")

    runtime.store.get_session = broken
    r = alice.api("GET", "/api/analyses")
    assert r.status_code == 503
