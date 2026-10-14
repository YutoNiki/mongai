"""認証：署名つきの主張の検証、外部の識別から内部ユーザーIDへの変換、無効な主張の扱い。"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.api.main import app, get_store, get_user_directory, get_verifier
from core.auth.assertion import (
    ASSERTION_HEADER,
    AssertionVerifier,
    ExternalIdentity,
    InvalidAssertion,
)
from core.auth.directory import UserDirectory
from core.sample import SAMPLE_ANALYSIS_ID, install_sample
from core.store import Store
from core.store.users import UserStore
from tests.unit.auth_factory import Signer


@pytest.fixture
def signer(tmp_path: Path) -> Signer:
    return Signer(tmp_path / "authn.json")


def identity(
    sub: str = "alice", email: str | None = "alice@example.ac.jp", **kw: object
) -> ExternalIdentity:
    return ExternalIdentity.model_validate(
        {
            "issuer": "https://idp.example",
            "subject": sub,
            "email": email,
            "email_verified": True,
            **kw,
        }
    )


# ---------------------------------------------------------------- 主張の検証


def test_valid_assertion(signer: Signer) -> None:
    found = AssertionVerifier(signer.key_file).verify(signer.token("alice"))
    assert (found.issuer, found.subject, found.email) == (
        "https://idp.example",
        "alice",
        "alice@example.ac.jp",
    )


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 1},  # 期限切れ
        {"aud": "someone-else"},  # 宛先が違う
        {"iss": "mallory"},  # 発行者が違う
        {"exp": 2_000_000_000, "iat": 1_000_000_000},  # 有効期間が長すぎる
    ],
)
def test_invalid_claims_are_rejected(signer: Signer, claims: dict[str, Any]) -> None:
    with pytest.raises(InvalidAssertion):
        AssertionVerifier(signer.key_file).verify(signer.token("alice", **claims))


def test_tampered_or_foreign_signatures_are_rejected(signer: Signer, tmp_path: Path) -> None:
    verifier = AssertionVerifier(signer.key_file)
    header, payload, signature = signer.token().split(".")
    forged = Signer(tmp_path / "other.json", kid=signer.kid)  # 同じ kid の別の鍵
    for token in (
        f"{header}.{forged.token('mallory').split('.')[1]}.{signature}",  # 内容を差し替え
        forged.token(),  # 別の鍵で署名
        f"{header}.{payload}.",  # 署名なし
        "not-a-token",
    ):
        with pytest.raises(InvalidAssertion):
            verifier.verify(token)


def test_unconfigured_verifier_rejects_everything(signer: Signer) -> None:
    with pytest.raises(InvalidAssertion):
        AssertionVerifier(None).verify(signer.token())


def test_dev_key_is_refused_in_production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """J.2 #31：本番の構成（MONGAI_ENV=production）では開発用の鍵を使わず、起動もしない。"""
    dev = Signer(tmp_path / "dev.json", kid="dev-local")
    verifier = AssertionVerifier(dev.key_file)
    assert verifier.verify(dev.token()).subject == "alice"  # 開発では使える
    monkeypatch.setenv("MONGAI_ENV", "production")
    with pytest.raises(InvalidAssertion):
        verifier.verify(dev.token())
    with pytest.raises(RuntimeError):
        verifier.check_configuration()


# ---------------------------------------------------------------- 内部ユーザーIDへの変換


def test_directory_registers_allowed_domain_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGAI_ALLOWED_EMAIL_DOMAINS", "example.ac.jp, s.example.ac.jp")
    users = UserStore(tmp_path / "db.sqlite3")
    directory = UserDirectory(users)
    first = directory.resolve(identity())
    assert first.startswith("U-") and len(first) == 34
    assert directory.resolve(identity()) == first  # 同じ識別子は同じ利用者
    assert directory.resolve(identity("bob", "bob@s.example.ac.jp")) != first


@pytest.mark.parametrize(
    "who",
    [
        identity("eve", "eve@evil.example"),  # 許可していないドメイン
        identity(
            "eve", "eve@example.ac.jp", email_verified=False
        ),  # 確認されていないメールアドレス
        identity("eve", None),  # メールアドレスなし
    ],
)
def test_directory_refuses_unknown_users(
    who: ExternalIdentity, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGAI_ALLOWED_EMAIL_DOMAINS", "example.ac.jp")
    with pytest.raises(InvalidAssertion):
        UserDirectory(UserStore(tmp_path / "db.sqlite3")).resolve(who)


def test_directory_needs_allowed_domains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONGAI_ALLOWED_EMAIL_DOMAINS", raising=False)
    with pytest.raises(InvalidAssertion):
        UserDirectory(UserStore(tmp_path / "db.sqlite3")).resolve(identity())


def test_same_email_with_another_identity_is_not_linked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """メールアドレスが同じでも、別の識別子（別のプロバイダー）は同じ利用者にしない。"""
    monkeypatch.setenv("MONGAI_ALLOWED_EMAIL_DOMAINS", "example.ac.jp")
    directory = UserDirectory(UserStore(tmp_path / "db.sqlite3"))
    google = directory.resolve(identity())
    other = directory.resolve(identity().model_copy(update={"issuer": "https://sso.example"}))
    assert google != other


def test_directory_allows_subdomains_but_not_lookalikes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """許可したドメインのサブドメインは登録できる。部分一致（fakeokayama-u.ac.jp など）は登録しない。"""
    monkeypatch.setenv("MONGAI_ALLOWED_EMAIL_DOMAINS", "okayama-u.ac.jp")
    directory = UserDirectory(UserStore(tmp_path / "db.sqlite3"))
    assert directory.resolve(identity("a", "a@s.okayama-u.ac.jp")).startswith("U-")
    assert directory.resolve(identity("b", "B@A.B.OKAYAMA-U.AC.JP")).startswith("U-")
    for email in ("c@fakeokayama-u.ac.jp", "c@okayama-u.ac.jp.evil.com", "c@okayama-u.ac.j"):
        with pytest.raises(InvalidAssertion):
            directory.resolve(identity("c", email))


def test_existing_user_outside_allowed_domains_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既に登録した利用者でも、主張のメールアドレスが許可の範囲外・未確認なら拒否する。"""
    monkeypatch.setenv("MONGAI_ALLOWED_EMAIL_DOMAINS", "example.ac.jp")
    directory = UserDirectory(UserStore(tmp_path / "db.sqlite3"))
    directory.resolve(identity())
    with pytest.raises(InvalidAssertion):
        directory.resolve(identity(email="alice@evil.example"))
    with pytest.raises(InvalidAssertion):
        directory.resolve(identity(email_verified=False))


def test_disabled_user_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGAI_ALLOWED_EMAIL_DOMAINS", "example.ac.jp")
    users = UserStore(tmp_path / "db.sqlite3")
    directory = UserDirectory(users)
    user_id = directory.resolve(identity())
    users.set_status(user_id, "disabled")
    with pytest.raises(InvalidAssertion):
        directory.resolve(identity())


# ---------------------------------------------------------------- API での扱い


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signer: Signer) -> Iterator[TestClient]:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("MONGAI_ALLOWED_EMAIL_DOMAINS", "example.ac.jp")
    store = Store(tmp_path / "db.sqlite3")
    install_sample(store)
    verifier = AssertionVerifier(signer.key_file)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_verifier] = lambda: verifier
    app.dependency_overrides[get_user_directory] = lambda: UserDirectory(UserStore(store.path))
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_invalid_assertion_does_not_fall_back_to_guest(
    api: TestClient, signer: Signer, tmp_path: Path
) -> None:
    """J.2 #22：無効な主張では、ゲストにも開いている API でも 401（ゲストとしての 200 にならない）。"""
    assert api.get("/api/analyses").status_code == 200  # 主張なし＝ゲスト
    disabled_user = signer.token("mallory")
    api.get("/api/analyses", headers={ASSERTION_HEADER: disabled_user})
    users = UserStore(tmp_path / "db.sqlite3")
    found = users.find_by_identity("https://idp.example", "mallory")
    assert found is not None
    users.set_status(found.user_id, "disabled")
    for token in (
        signer.token(exp=1),
        signer.token(aud="other"),
        Signer(tmp_path / "x.json", kid=signer.kid).token(),
        signer.token("eve", email="eve@evil.example"),
        disabled_user,
        "garbage",
    ):
        for path in ("/api/analyses", f"/api/analyses/{SAMPLE_ANALYSIS_ID}"):
            r = api.get(path, headers={ASSERTION_HEADER: token})
            assert r.status_code == 401 and r.json()["detail"] == "invalid_session"


def test_valid_assertion_identifies_the_user(api: TestClient, signer: Signer) -> None:
    headers = {ASSERTION_HEADER: signer.token("alice")}
    assert api.get("/api/usage/today", headers=headers).status_code == 200
    assert api.get("/api/usage/today").status_code == 401  # 主張なしはゲスト
    assert api.get("/api/healthz", headers={ASSERTION_HEADER: "garbage"}).status_code == 200
