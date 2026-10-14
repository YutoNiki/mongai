"""許可するメールのドメインの判定（authn と core で共通）：許可したドメインとそのサブドメインだけ。"""

import pytest

from shared.email_domain import (
    domain_allowed,
    email_allowed,
    email_domain,
    parse_allowed_domains,
)

ALLOWED = parse_allowed_domains("okayama-u.ac.jp")


@pytest.mark.parametrize(
    "email",
    [
        "user@okayama-u.ac.jp",
        "user@s.okayama-u.ac.jp",
        "user@sub.okayama-u.ac.jp",
        "user@a.b.okayama-u.ac.jp",
        "USER@S.OKAYAMA-U.AC.JP",
        "first.last+tag@s.okayama-u.ac.jp",
    ],
)
def test_allowed(email: str) -> None:
    assert email_allowed(email, ALLOWED)


@pytest.mark.parametrize(
    "email",
    [
        "user@gmail.com",
        "user@other.ac.jp",
        "user@fakeokayama-u.ac.jp",
        "user@okayama-u.ac.jp.evil.com",
        "user@okayama-u.ac.jp.example.com",
        "user@okayama-u.ac.j",
        "user@okayama-u-ac-jp.com",
        "user@",
        "@okayama-u.ac.jp",
        "invalid-email",
        "user@@okayama-u.ac.jp",
        "a@b@okayama-u.ac.jp",
        "user@okayama-u.ac.jp.",
        "user@.okayama-u.ac.jp",
        "user@s..okayama-u.ac.jp",
        "user@-s.okayama-u.ac.jp",
        "user name@okayama-u.ac.jp",
        '"user"@okayama-u.ac.jp',
        "user@okayama-u.ac.jp\n",
        "",
    ],
)
def test_denied(email: str) -> None:
    assert not email_allowed(email, ALLOWED)


def test_none_and_empty_allowlist() -> None:
    assert not email_allowed(None, ALLOWED)
    assert not email_allowed("user@okayama-u.ac.jp", frozenset())


def test_domain_rules() -> None:
    assert domain_allowed("okayama-u.ac.jp", ALLOWED)
    assert domain_allowed("S.Okayama-U.ac.jp", ALLOWED)
    assert not domain_allowed("fakeokayama-u.ac.jp", ALLOWED)
    assert not domain_allowed("ac.jp", ALLOWED)
    assert not domain_allowed("", ALLOWED)
    assert not domain_allowed(None, ALLOWED)
    assert email_domain("User@S.Okayama-U.ac.jp") == "s.okayama-u.ac.jp"


def test_parse_ignores_malformed_entries() -> None:
    assert parse_allowed_domains(" okayama-u.ac.jp , @example.ac.jp, , *, jp, a..b") == {
        "okayama-u.ac.jp",
        "example.ac.jp",
    }
