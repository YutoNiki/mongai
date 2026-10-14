"""開発用のモック認証サービス（devauth）：core の検証を通る主張を返し、本番の構成では使えない。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.auth.assertion import AssertionVerifier, InvalidAssertion


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DEVAUTH_PUBLIC_KEY_FILE", str(tmp_path / "devauth.json"))
    from devauth.main import app

    return TestClient(app)


def test_devauth_issues_assertions_core_accepts(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with client:  # 起動時に公開鍵を書く
        assert client.get("/auth/verify").headers.get("X-MongAI-Assertion") is None  # ゲスト
        login = client.get("/auth/dev-login", params={"user": "alice"}, follow_redirects=False)
        assert login.status_code == 303
        token = client.get("/auth/verify").headers["X-MongAI-Assertion"]
        verifier = AssertionVerifier(tmp_path / "devauth.json")
        found = verifier.verify(token)
        assert (found.subject, found.email, found.email_verified) == (
            "alice",
            "alice@example.ac.jp",
            True,
        )
        # 本番の構成の core は、開発用の鍵（dev-）の主張を受け付けない
        monkeypatch.setenv("MONGAI_ENV", "production")
        with pytest.raises(InvalidAssertion):
            verifier.verify(token)
        client.cookies.set("mongai_dev_user", "../bad")
        assert client.get("/auth/verify").status_code == 401  # 不正なセッションはゲストにしない
        client.get("/auth/dev-logout", follow_redirects=False)
        client.cookies.clear()
        assert client.get("/auth/verify").headers.get("X-MongAI-Assertion") is None
        assert client.get("/auth/dev-login", params={"user": "Bad User"}).status_code == 422
