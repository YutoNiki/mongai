"""認証サービス（authn）：Google の OIDC のログイン・セッション・ログアウト・鍵（偽の Google を使い、外部と通信しない）。"""

import logging
import sqlite3
import stat
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from authn.config import ConfigError, Settings
from authn.keys import (
    PRIVATE_KEY_NAME,
    SigningKey,
    SigningKeyError,
    ensure_signing_key,
    load_private_key,
    rotate_signing_key,
)
from authn.main import create_app, safe_next
from authn.store import StoreError, digest
from core.auth.assertion import AssertionVerifier, InvalidAssertion
from tests.unit.fake_google import CLIENT_ID, CLIENT_SECRET, FakeGoogle, new_key

ORIGIN = "http://localhost:8080"
SESSION = "mongai_session"
FLOW = "mongai_login"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": f"{ORIGIN}/auth/callback",
        "allowed_domains": frozenset({"okayama-u.ac.jp"}),
        "data_dir": tmp_path / "authn",
        "public_key_file": tmp_path / "keys" / "authn.json",
        **overrides,
    }
    return Settings(**values)


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings: Settings, google: FakeGoogle) -> Iterator[TestClient]:
    with TestClient(create_app(settings, google.transport()), base_url=ORIGIN) as c:
        yield c


def start(client: TestClient, path: str = "/auth/login") -> str:
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303, r.text
    return str(r.headers["location"])


def login(client: TestClient, google: FakeGoogle, **claims: Any) -> Any:
    """ログインを始め、偽の Google で認可して、コールバックの応答を返す。"""
    code, state = google.authorize(start(client), **claims)
    return client.get(
        "/auth/callback", params={"state": state, "code": code}, follow_redirects=False
    )


def auth_error(response: Any) -> str | None:
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    errors = query.get("auth_error")
    return str(errors[0]) if errors else None


def assertion(client: TestClient) -> str | None:
    r = client.get("/auth/verify")
    assert r.status_code == 200
    token = r.headers.get("X-MongAI-Assertion")
    return None if token is None else str(token)


def db(settings: Settings) -> sqlite3.Connection:
    return sqlite3.connect(settings.data_dir / "sessions.db")


# ---------------------------------------------------------------- 正常なログイン


def test_login_redirects_to_google_with_state_nonce_and_pkce(client: TestClient) -> None:
    r = client.get("/auth/login", follow_redirects=False)
    url = urlsplit(r.headers["location"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    assert query["scope"] == "openid email"  # profile は求めない
    assert query["redirect_uri"] == f"{ORIGIN}/auth/callback"  # 要求の Host ではなく設定値
    assert query["code_challenge_method"] == "S256" and query["code_challenge"]
    assert len(query["state"]) >= 40 and len(query["nonce"]) >= 40
    # アカウント選択の補助（判定には使わない）。特定のドメインにすると、Google の画面が
    # メールの末尾をそのドメインに固定し、s.okayama-u.ac.jp の学生が入力できなくなる
    assert query["hd"] == "*"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{FLOW}=") and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "client_secret" not in r.headers["location"]


def test_successful_login_creates_a_session_and_core_accepts_the_assertion(
    client: TestClient, google: FakeGoogle, settings: Settings
) -> None:
    assert assertion(client) is None  # ログイン前はゲスト
    r = login(client, google)
    assert r.headers["location"] == "/" and auth_error(r) is None
    cookies = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith(f"{SESSION}="))
    for attr in ("HttpOnly", "Path=/", "SameSite=lax", "Max-Age=28800"):
        assert attr in session_cookie
    assert "Secure" not in session_cookie  # http://localhost では Secure を付けられない
    assert any(c.startswith(f'{FLOW}=""') for c in cookies)  # ログインの途中の Cookie は消す
    session_id = client.cookies[SESSION]
    assert len(session_id) == 43 and "@" not in session_id  # Cookie にはランダムな ID だけ

    token = assertion(client)
    assert token is not None
    found = AssertionVerifier(settings.public_key_file).verify(token)
    assert (found.issuer, found.subject, found.email, found.email_verified) == (
        "https://accounts.google.com",
        "100000000000000000001",
        "taro@s.okayama-u.ac.jp",
        True,
    )
    # サーバー側には ID のハッシュだけを保存し、Google のトークンは保存しない
    with db(settings) as conn:
        rows = conn.execute("SELECT * FROM sessions").fetchall()
    assert len(rows) == 1 and rows[0][0] == digest(session_id)
    dump = repr(rows)
    assert session_id not in dump and "access-" not in dump and "eyJ" not in dump


@pytest.mark.parametrize(
    "email,hd",
    [
        ("user@okayama-u.ac.jp", "okayama-u.ac.jp"),
        ("user@s.okayama-u.ac.jp", "okayama-u.ac.jp"),  # メールと hd のドメインは違ってよい
        ("user@a.b.okayama-u.ac.jp", "s.okayama-u.ac.jp"),
        ("USER@S.OKAYAMA-U.AC.JP", "OKAYAMA-U.AC.JP"),
    ],
)
def test_university_accounts_are_accepted(
    client: TestClient, google: FakeGoogle, email: str, hd: str
) -> None:
    assert auth_error(login(client, google, email=email, hd=hd)) is None
    assert assertion(client) is not None


def test_issuer_without_scheme_is_normalized(
    client: TestClient, google: FakeGoogle, settings: Settings
) -> None:
    """Google は iss を accounts.google.com とも書く。利用者の対応付けがぶれないよう、同じ値にそろえる。"""
    login(client, google, iss="accounts.google.com")
    token = assertion(client)
    assert token is not None
    assert AssertionVerifier(settings.public_key_file).verify(token).issuer == (
        "https://accounts.google.com"
    )


# ---------------------------------------------------------------- 許可しないアカウント


@pytest.mark.parametrize(
    "claims",
    [
        {"email": "user@gmail.com", "hd": None},
        {"email": "user@gmail.com"},
        {"email": "user@other.ac.jp", "hd": "other.ac.jp"},
        {"email": "user@fakeokayama-u.ac.jp"},
        {"email": "user@okayama-u.ac.jp.evil.com"},
        {"email": "user@okayama-u.ac.j"},
        {"email": "user@"},
        {"email": "@okayama-u.ac.jp"},
        {"email": "invalid-email"},
        {"email": None},
        {"email_verified": False},
        {"email_verified": "true"},  # 真偽値の true だけを受け付ける
        {"email_verified": None},
        {"hd": None},  # 組織のアカウントでない
        {"hd": "gmail.com"},
        {"hd": "fakeokayama-u.ac.jp"},
        {"hd": "okayama-u.ac.jp.evil.com"},
    ],
)
def test_accounts_outside_the_university_are_denied(
    client: TestClient, google: FakeGoogle, settings: Settings, claims: dict[str, Any]
) -> None:
    r = login(client, google, **claims)
    assert auth_error(r) == "denied"
    assert not any(c.startswith(f"{SESSION}=") for c in r.headers.get_list("set-cookie"))
    assert assertion(client) is None  # ゲストのまま
    with db(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_denied_login_logs_only_safe_attributes(
    client: TestClient, google: FakeGoogle, caplog: pytest.LogCaptureFixture
) -> None:
    """拒否の理由を調べられるよう、クレームの名前・hd・メールのドメインだけを記録する（トークン・sub は記録しない）。"""
    with caplog.at_level(logging.INFO):
        login(client, google, email="secret.person@gmail.com", hd=None)
    # authn のログと、authn から Google への要求のログ（テストのブラウザ役の要求のログは除く）
    text = "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("authn") or "https://" in r.getMessage()
    )
    assert "hd" in text and "gmail.com" in text and "email_verified" in text
    assert "secret.person" not in text and "100000000000000000001" not in text
    assert "eyJ" not in text and "code-1" not in text and CLIENT_SECRET not in text


# ---------------------------------------------------------------- 不正なトークン


def test_tokens_with_invalid_claims_or_signature_are_rejected(
    client: TestClient, google: FakeGoogle
) -> None:
    now = int(time.time())
    cases: list[dict[str, Any]] = [
        {"aud": "someone-else.apps.googleusercontent.example"},
        {"aud": ["someone-else"], "azp": "someone-else"},
        {"iss": "https://evil.example"},
        {"exp": now - 3600, "iat": now - 7200},  # 期限切れ
        {"iat": now - 3600},  # 古すぎる発行
        {"iat": now + 3600},  # 未来の発行
        {"sub": None},
        {"sub": ""},
        {"nonce": "another-nonce"},
        {"nonce": None},
        {"azp": "someone-else"},
    ]
    for claims in cases:
        assert auth_error(login(client, google, **claims)) == "failed", claims
        assert assertion(client) is None, claims


def test_token_signed_with_an_unknown_key_is_rejected(
    client: TestClient, google: FakeGoogle
) -> None:
    attacker = new_key(google.key.kid or "google-1")  # 同じ kid の別の鍵
    code, state = google.authorize(start(client))
    nonce = google.codes[code]["nonce"]
    google.codes[code]["overrides"] = {"_id_token": google.sign(google.claims(nonce), attacker)}
    r = client.get("/auth/callback", params={"state": state, "code": code}, follow_redirects=False)
    assert auth_error(r) == "failed"
    assert assertion(client) is None


def test_unsigned_token_is_rejected(client: TestClient, google: FakeGoogle) -> None:
    code, state = google.authorize(start(client))
    header, payload, _ = google.sign(google.claims(google.codes[code]["nonce"])).split(".")
    google.codes[code]["overrides"] = {"_id_token": f"{header}.{payload}."}
    r = client.get("/auth/callback", params={"state": state, "code": code}, follow_redirects=False)
    assert auth_error(r) == "failed"


def test_google_key_rotation_is_followed(client: TestClient, google: FakeGoogle) -> None:
    assert auth_error(login(client, google)) is None  # 公開鍵をキャッシュする
    google.key = new_key("google-2")
    assert auth_error(login(client, google)) is None  # 知らない kid なら取り直す


# ---------------------------------------------------------------- state・コールバック


def test_state_mismatch_is_rejected(client: TestClient, google: FakeGoogle) -> None:
    code, _ = google.authorize(start(client))
    r = client.get(
        "/auth/callback", params={"state": "x" * 43, "code": code}, follow_redirects=False
    )
    assert auth_error(r) == "expired"
    assert assertion(client) is None


def test_direct_access_to_callback_is_rejected(
    client: TestClient, google: FakeGoogle, settings: Settings
) -> None:
    for params in ({}, {"code": "code-1"}, {"state": "x" * 43, "code": "code-1"}):
        r = client.get("/auth/callback", params=params, follow_redirects=False)
        assert auth_error(r) == "expired"
    assert not any(r.method == "POST" for r in google.requests)  # Google に交換を求めない


def test_callback_in_another_browser_is_rejected(
    client: TestClient, google: FakeGoogle, settings: Settings
) -> None:
    """state だけを盗んでも、ログインを始めたブラウザの Cookie が無ければ使えない（ログインの CSRF 対策）。"""
    code, state = google.authorize(start(client))
    client.cookies.set(FLOW, "y" * 43)
    r = client.get("/auth/callback", params={"state": state, "code": code}, follow_redirects=False)
    assert auth_error(r) == "expired"
    assert assertion(client) is None


def test_callback_cannot_be_replayed(client: TestClient, google: FakeGoogle) -> None:
    code, state = google.authorize(start(client))
    first = client.get(
        "/auth/callback", params={"state": state, "code": code}, follow_redirects=False
    )
    assert auth_error(first) is None
    client.cookies.delete(SESSION)
    replay = client.get(
        "/auth/callback", params={"state": state, "code": code}, follow_redirects=False
    )
    assert auth_error(replay) == "expired"
    assert assertion(client) is None


def test_authorization_code_cannot_be_reused(client: TestClient, google: FakeGoogle) -> None:
    code, state = google.authorize(start(client))
    assert (
        auth_error(
            client.get(
                "/auth/callback", params={"state": state, "code": code}, follow_redirects=False
            )
        )
        is None
    )
    client.cookies.delete(SESSION)
    # 新しいログインの state に、使用済みの認可コードを組み合わせる → Google が invalid_grant を返す
    _, state2 = google.authorize(start(client))
    r = client.get("/auth/callback", params={"state": state2, "code": code}, follow_redirects=False)
    assert auth_error(r) == "failed"
    assert assertion(client) is None


def test_interrupted_login_expires(
    client: TestClient, google: FakeGoogle, settings: Settings
) -> None:
    code, state = google.authorize(start(client))
    with db(settings) as conn:
        conn.execute("UPDATE login_flows SET created_at = created_at - 3600")
    r = client.get("/auth/callback", params={"state": state, "code": code}, follow_redirects=False)
    assert auth_error(r) == "expired"


def test_cancelled_at_google(client: TestClient, google: FakeGoogle) -> None:
    _, state = google.authorize(start(client))
    r = client.get(
        "/auth/callback",
        params={"state": state, "error": "access_denied"},
        follow_redirects=False,
    )
    assert auth_error(r) == "cancelled"
    assert assertion(client) is None


def test_google_unreachable(client: TestClient, google: FakeGoogle) -> None:
    google.fail_network = True
    r = client.get("/auth/login", follow_redirects=False)
    assert auth_error(r) == "unavailable"
    google.fail_network = False
    code, state = google.authorize(start(client))
    google.fail_network = True
    r = client.get("/auth/callback", params={"state": state, "code": code}, follow_redirects=False)
    assert auth_error(r) == "unavailable"
    assert assertion(client) is None


def test_unexpected_endpoints_in_discovery_are_refused(tmp_path: Path, google: FakeGoogle) -> None:
    google.metadata["token_endpoint"] = "https://evil.example/token"
    with TestClient(create_app(make_settings(tmp_path), google.transport()), base_url=ORIGIN) as c:
        assert auth_error(c.get("/auth/login", follow_redirects=False)) == "unavailable"


# ---------------------------------------------------------------- リダイレクト先


@pytest.mark.parametrize(
    "next_,expected",
    [
        ("/analyses/A-0123456789ab", "/analyses/A-0123456789ab"),
        ("/rules", "/rules"),
        ("https://evil.example/", "/"),
        ("//evil.example/", "/"),
        ("/\\evil.example", "/"),
        ("/api/documents", "/"),
        ("/auth/logout", "/"),
        ("/../etc/passwd", "/"),
        ("javascript:alert(1)", "/"),
        (None, "/"),
    ],
)
def test_safe_next(next_: str | None, expected: str) -> None:
    assert safe_next(next_) == expected


def test_login_never_redirects_outside_the_app(client: TestClient, google: FakeGoogle) -> None:
    code, state = google.authorize(start(client, "/auth/login?next=https://evil.example/"))
    r = client.get("/auth/callback", params={"state": state, "code": code}, follow_redirects=False)
    assert r.headers["location"] == "/"
    code, state = google.authorize(start(client, "/auth/login?next=/analyses/A-1"))
    r = client.get("/auth/callback", params={"state": state, "code": code}, follow_redirects=False)
    assert r.headers["location"] == "/analyses/A-1"


def test_login_from_another_host_name_moves_to_the_configured_origin(
    client: TestClient,
) -> None:
    """127.0.0.1 で開いても、Cookie とコールバックの URL がそろうよう、設定したオリジンでやり直す。"""
    r = client.get("http://127.0.0.1:8080/auth/login?next=/rules", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"{ORIGIN}/auth/login?next=%2Frules"
    r = client.get("/auth/login", headers={"Host": "evil.example"}, follow_redirects=False)
    assert r.headers["location"].startswith(f"{ORIGIN}/")


# ---------------------------------------------------------------- セッションの確認


def test_invalid_cookies_are_rejected_not_treated_as_guest(
    client: TestClient, google: FakeGoogle
) -> None:
    for value in ("garbage", "x" * 43, "'; DROP TABLE sessions; --", ""):
        client.cookies.set(SESSION, value)
        assert client.get("/auth/verify").status_code == 401, value
    client.cookies.clear()
    session_id = session_from(login(client, google))
    tampered = session_id[:-1] + ("A" if session_id[-1] != "A" else "B")
    use_session(client, tampered)
    assert client.get("/auth/verify").status_code == 401
    use_session(client, session_id)
    assert assertion(client) is not None


def test_session_expiry(client: TestClient, google: FakeGoogle, settings: Settings) -> None:
    login(client, google)
    assert assertion(client) is not None
    with db(settings) as conn:
        conn.execute("UPDATE sessions SET expires_at = 1")
    assert client.get("/auth/verify").status_code == 401
    with db(settings) as conn:  # 期限の切れたセッションは消す
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_idle_session_expires(client: TestClient, google: FakeGoogle, settings: Settings) -> None:
    login(client, google)
    with db(settings) as conn:
        conn.execute("UPDATE sessions SET last_seen_at = last_seen_at - ?", (3 * 3600,))
    assert client.get("/auth/verify").status_code == 401


def session_from(response: Any) -> str:
    """応答の Set-Cookie から、発行されたセッション ID を取り出す。"""
    cookie = next(c for c in response.headers.get_list("set-cookie") if c.startswith(f"{SESSION}="))
    return str(cookie.split(";")[0].split("=", 1)[1])


def use_session(client: TestClient, session_id: str) -> None:
    client.cookies.clear()
    client.cookies.set(SESSION, session_id)


def test_new_login_replaces_the_previous_session(
    client: TestClient, google: FakeGoogle, settings: Settings
) -> None:
    """セッションの固定化の防止：ログインのたびに新しい ID にし、前の ID は使えなくする。"""
    planted = "z" * 43  # 攻撃者が仕込んだ値
    use_session(client, planted)
    first = session_from(login(client, google))
    assert first != planted
    use_session(client, planted)
    assert client.get("/auth/verify").status_code == 401  # 仕込んだ値はセッションにならない
    use_session(client, first)
    second = session_from(login(client, google))
    assert second != first
    use_session(client, first)
    assert client.get("/auth/verify").status_code == 401  # 前のセッションは消える
    use_session(client, second)
    assert assertion(client) is not None


def test_store_failure_fails_closed(client: TestClient, google: FakeGoogle) -> None:
    login(client, google)
    runtime = client.app.state.runtime  # type: ignore[attr-defined]

    def broken(*_: Any, **__: Any) -> Any:
        raise StoreError("偽の障害")

    original = runtime.store.get_session
    runtime.store.get_session = broken
    r = client.get("/auth/verify")
    assert r.status_code == 503 and "X-MongAI-Assertion" not in r.headers
    runtime.store.get_session = original
    runtime.store.create_flow = broken
    assert auth_error(client.get("/auth/login", follow_redirects=False)) == "unavailable"


def test_narrowing_allowed_domains_invalidates_sessions(
    client: TestClient, google: FakeGoogle, tmp_path: Path
) -> None:
    login(client, google)
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    runtime.settings = make_settings(tmp_path, allowed_domains=frozenset({"other.ac.jp"}))
    assert client.get("/auth/verify").status_code == 401


def test_without_google_configuration_nobody_can_log_in(tmp_path: Path, google: FakeGoogle) -> None:
    settings = make_settings(tmp_path, client_id="", client_secret="")
    with TestClient(create_app(settings, google.transport()), base_url=ORIGIN) as c:
        assert auth_error(c.get("/auth/login", follow_redirects=False)) == "not_configured"
        assert auth_error(c.get("/auth/callback", follow_redirects=False)) == "not_configured"
        assert c.get("/auth/verify").headers.get("X-MongAI-Assertion") is None  # ゲスト
        c.cookies.set(SESSION, "x" * 43)
        assert c.get("/auth/verify").status_code == 401
    assert google.requests == []
    assert settings.public_key_file.exists()  # core が読む公開鍵はある（主張は作られない）


def test_responses_are_not_cached(client: TestClient) -> None:
    for path in ("/auth/verify", "/auth/login"):
        assert client.get(path, follow_redirects=False).headers["cache-control"] == "no-store"


# ---------------------------------------------------------------- ログアウト


def test_logout_invalidates_the_session_on_the_server(
    client: TestClient, google: FakeGoogle
) -> None:
    login(client, google)
    stolen = client.cookies[SESSION]
    r = client.post("/auth/logout", headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 204
    assert any(
        c.startswith(f'{SESSION}=""') and "Max-Age=0" in c for c in r.headers.get_list("set-cookie")
    )
    assert assertion(client) is None  # ゲストに戻る
    client.cookies.set(SESSION, stolen)  # 盗まれた Cookie でも使えない
    assert client.get("/auth/verify").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},  # Origin なし
        {"Origin": "https://evil.example"},
        {"Origin": "http://127.0.0.1:8080"},
        {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
        {"Origin": ORIGIN, "Sec-Fetch-Site": "same-site"},
    ],
)
def test_logout_requires_same_origin(
    client: TestClient, google: FakeGoogle, headers: dict[str, str]
) -> None:
    login(client, google)
    assert client.post("/auth/logout", headers=headers).status_code == 403
    assert assertion(client) is not None  # ログインしたまま


def test_logout_that_cannot_delete_the_session_keeps_the_cookie(
    client: TestClient, google: FakeGoogle
) -> None:
    """サーバー側で消せなかったら 503 で、Cookie も残す（「ログアウトした」と誤解させず、やり直せるようにする）。"""
    login(client, google)
    runtime = client.app.state.runtime  # type: ignore[attr-defined]

    def broken(*_: Any, **__: Any) -> Any:
        raise StoreError("偽の障害")

    original = runtime.store.delete_session
    runtime.store.delete_session = broken
    r = client.post("/auth/logout", headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 503
    assert not any(c.startswith(f"{SESSION}=") for c in r.headers.get_list("set-cookie"))
    runtime.store.delete_session = original
    r = client.post("/auth/logout", headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 204
    assert assertion(client) is None


def test_logout_by_get_is_not_allowed(client: TestClient, google: FakeGoogle) -> None:
    login(client, google)
    assert client.get("/auth/logout").status_code == 405
    assert assertion(client) is not None


# ---------------------------------------------------------------- Secure の Cookie（HTTPS）


def test_https_uses_secure_host_prefixed_cookies(tmp_path: Path, google: FakeGoogle) -> None:
    settings = make_settings(tmp_path, redirect_uri="https://si-nsa.example.ac.jp/auth/callback")
    with TestClient(
        create_app(settings, google.transport()), base_url="https://si-nsa.example.ac.jp"
    ) as c:
        code, state = google.authorize(start(c))
        r = c.get("/auth/callback", params={"state": state, "code": code}, follow_redirects=False)
        cookie = next(
            x for x in r.headers.get_list("set-cookie") if x.startswith("__Host-mongai_session=")
        )
        assert "Secure" in cookie and "Path=/" in cookie and "Domain" not in cookie


# ---------------------------------------------------------------- 設定


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://si-nsa.example.ac.jp/auth/callback",  # HTTP は localhost だけ
        "http://localhost:8080/callback",
        "http://localhost:8080/auth/callback?next=/",
        "ftp://localhost/auth/callback",
        "localhost:8080/auth/callback",
    ],
)
def test_invalid_redirect_uri_is_refused(tmp_path: Path, redirect_uri: str) -> None:
    with pytest.raises(ConfigError):
        make_settings(tmp_path, redirect_uri=redirect_uri)


def test_settings_repr_hides_the_secret(settings: Settings) -> None:
    assert CLIENT_SECRET not in repr(settings)


# ---------------------------------------------------------------- 署名の鍵


def test_signing_key_is_persistent_private_and_separate(settings: Settings) -> None:
    first = ensure_signing_key(settings.data_dir, settings.public_key_file)
    again = ensure_signing_key(settings.data_dir, settings.public_key_file)
    assert first.kid == again.kid and first.kid.startswith("authn-")  # 再起動しても同じ鍵
    private = settings.data_dir / PRIVATE_KEY_NAME
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    public = settings.public_key_file.read_text()
    assert "private_key" not in public and first.kid in public


def test_rotated_key_is_used_by_core(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGAI_ENV", "production")
    old = ensure_signing_key(settings.data_dir, settings.public_key_file)
    verifier = AssertionVerifier(settings.public_key_file)
    verifier.verify(
        old.sign_assertion(
            idp="https://accounts.google.com", subject="s", email="a@okayama-u.ac.jp"
        )
    )
    time.sleep(0.01)
    new = rotate_signing_key(settings.data_dir, settings.public_key_file)
    assert new.kid != old.kid
    verifier.verify(
        new.sign_assertion(
            idp="https://accounts.google.com", subject="s", email="a@okayama-u.ac.jp"
        )
    )
    with pytest.raises(InvalidAssertion):  # 古い鍵の主張は使えない
        verifier.verify(
            old.sign_assertion(
                idp="https://accounts.google.com", subject="s", email="a@okayama-u.ac.jp"
            )
        )


def test_development_keys_are_never_loaded(settings: Settings) -> None:
    dev = SigningKey("dev-0000", SigningKey.generate().key)
    path = settings.data_dir / PRIVATE_KEY_NAME
    path.parent.mkdir(parents=True)
    path.write_text(dev.private_json())
    with pytest.raises(SigningKeyError):
        load_private_key(path)
    with pytest.raises(SigningKeyError):
        ensure_signing_key(settings.data_dir, settings.public_key_file)


# ---------------------------------------------------------------- 運用のコマンド


def test_manage_commands(
    client: TestClient,
    google: FakeGoogle,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from authn.manage import main

    monkeypatch.setenv("AUTHN_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("AUTHN_PUBLIC_KEY_FILE", str(settings.public_key_file))
    login(client, google)
    before = load_private_key(settings.data_dir / PRIVATE_KEY_NAME).kid
    assert main(["rotate-key"]) == 0
    assert load_private_key(settings.data_dir / PRIVATE_KEY_NAME).kid != before
    assert main(["revoke-sessions"]) == 0
    assert "1 件" in capsys.readouterr().out
    assert client.get("/auth/verify").status_code == 401  # 全員がログインし直す
