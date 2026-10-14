"""認証：署名つきの主張の検証、外部の識別から内部ユーザーIDへの変換、無効な主張の扱い。"""

from pathlib import Path
from typing import Any

import pytest

from core.auth.assertion import AssertionVerifier, ExternalIdentity, InvalidAssertion
from core.auth.directory import UserDirectory
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


def test_disabled_user_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGAI_ALLOWED_EMAIL_DOMAINS", "example.ac.jp")
    users = UserStore(tmp_path / "db.sqlite3")
    directory = UserDirectory(users)
    user_id = directory.resolve(identity())
    users.set_status(user_id, "disabled")
    with pytest.raises(InvalidAssertion):
        directory.resolve(identity())
