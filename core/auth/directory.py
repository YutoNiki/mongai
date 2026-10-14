"""外部の識別（認証プロバイダーの issuer・subject）から内部ユーザーIDへの変換。

- 既に対応付けた利用者なら、その内部ユーザーID（無効化された利用者は拒否）
- 初めての利用者は、確認済みのメールアドレスが許可したドメイン（環境変数 MONGAI_ALLOWED_EMAIL_DOMAINS、
  カンマ区切り）か、そのサブドメインのときだけ、自動で登録する。許可するドメインが無ければ新しい利用者は作らない
- 許可するドメインを設定しているときは、既に対応付けた利用者でも、主張のメールアドレスが許可の範囲外なら拒否する
  （認証サービスでも同じ規則で確かめている。core でも確かめるのは、設定の誤りで範囲外の利用者を通さないため）
- メールアドレスが同じでも、別の識別子を同じ利用者に自動で結び付けない
"""

import os
from datetime import UTC, datetime, timedelta

from core.auth.assertion import ExternalIdentity, InvalidAssertion
from core.store.users import UserStore
from shared.email_domain import email_allowed, parse_allowed_domains

# 最後のログイン日時を書き込む間隔（要求のたびに書き込まない）
LOGIN_RECORD_INTERVAL = timedelta(hours=1)


def allowed_domains() -> frozenset[str]:
    return parse_allowed_domains(os.environ.get("MONGAI_ALLOWED_EMAIL_DOMAINS", ""))


def _email_ok(identity: ExternalIdentity, allowed: frozenset[str]) -> bool:
    return identity.email_verified and email_allowed(identity.email, allowed)


class UserDirectory:
    def __init__(self, users: UserStore | None = None) -> None:
        self.users = users or UserStore()

    def resolve(self, identity: ExternalIdentity) -> str:
        allowed = allowed_domains()
        user = self.users.find_by_identity(identity.issuer, identity.subject)
        if user is not None:
            if user.status != "active":
                raise InvalidAssertion("無効化された利用者")
            if allowed and not _email_ok(identity, allowed):
                raise InvalidAssertion("許可の範囲外のメールアドレス")
            last = datetime.fromisoformat(user.last_login_at) if user.last_login_at else None
            if last is None or datetime.now(UTC) - last >= LOGIN_RECORD_INTERVAL:
                self.users.record_login(
                    user.user_id,
                    identity.issuer,
                    identity.subject,
                    display_name=identity.name,
                    email=identity.email,
                )
            return user.user_id
        if not _email_ok(identity, allowed):
            raise InvalidAssertion("登録が許可されていない利用者")
        return self.users.create_with_identity(
            identity.issuer, identity.subject, display_name=identity.name, email=identity.email
        )
