"""メールアドレス・組織のドメインが、許可したドメイン（とそのサブドメイン）に含まれるかの判定。

認証サービス（authn）と core の両方で同じ規則を使う。文字列の部分一致では判定しない：
許可したドメインが `okayama-u.ac.jp` なら、`okayama-u.ac.jp` と `*.okayama-u.ac.jp` だけを許可し、
`fakeokayama-u.ac.jp` や `okayama-u.ac.jp.example.com` は許可しない。大文字・小文字は区別しない。
"""

import re

# ドメインの各ラベル（英数字とハイフン。先頭と末尾はハイフン以外、63文字まで）
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
# メールアドレスのローカル部（RFC 5322 の dot-atom。引用符つきの形式は受け付けない）
_LOCAL = re.compile(r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*")


def normalize_domain(value: str) -> str | None:
    """ドメインを小文字にして返す。形式が正しくなければ None。"""
    domain = value.lower()
    if not domain or len(domain) > 253 or domain.endswith("."):
        return None
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        return None
    if labels[-1].isdigit():  # IP アドレスの形は受け付けない
        return None
    return domain


def email_domain(email: str) -> str | None:
    """メールアドレスのドメイン部（小文字）。`@` がちょうど1つで、両側が正しい形式のときだけ返す。"""
    value = email.lower()
    if value.count("@") != 1 or len(value) > 320:
        return None
    local, domain = value.split("@")
    if not _LOCAL.fullmatch(local) or len(local) > 64:
        return None
    return normalize_domain(domain)


def parse_allowed_domains(raw: str) -> frozenset[str]:
    """カンマ区切りの許可するドメイン。形式の正しくないものは無視する（許可を広げない）。"""
    domains = (normalize_domain(d.strip().lstrip("@")) for d in raw.split(","))
    return frozenset(d for d in domains if d)


def domain_allowed(domain: str | None, allowed: frozenset[str]) -> bool:
    """ドメインが、許可したドメインそのものか、そのサブドメインか。"""
    if domain is None:
        return False
    normalized = normalize_domain(domain)
    if normalized is None:
        return False
    return any(normalized == a or normalized.endswith("." + a) for a in allowed)


def email_allowed(email: str | None, allowed: frozenset[str]) -> bool:
    return email is not None and domain_allowed(email_domain(email), allowed)
