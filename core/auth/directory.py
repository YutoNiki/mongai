"""外部の識別（認証プロバイダーの issuer・subject）から内部ユーザーIDへの変換。

- 既に対応付けた利用者なら、その内部ユーザーID（無効化された利用者は拒否）
- 初めての利用者は、確認済みのメールアドレスが許可したドメイン（環境変数 MONGAI_ALLOWED_EMAIL_DOMAINS、
  カンマ区切り）に一致するときだけ、自動で登録する。許可するドメインが無ければ新しい利用者は作らない
- メールアドレスが同じでも、別の識別子を同じ利用者に自動で結び付けない
"""

import os
from datetime import UTC, datetime, timedelta

from core.auth.assertion import ExternalIdentity, InvalidAssertion
from core.store.users import UserStore

# 最後のログイン日時を書き込む間隔（要求のたびに書き込まない）
LOGIN_RECORD_INTERVAL = timedelta(hours=1)


def allowed_domains() -> frozenset[str]:
    raw = os.environ.get("MONGAI_ALLOWED_EMAIL_DOMAINS", "")
    return frozenset(d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip())


class UserDirectory:
    def __init__(self, users: UserStore | None = None) -> None:
        self.users = users or UserStore()

    def resolve(self, identity: ExternalIdentity) -> str:
        user = self.users.find_by_identity(identity.issuer, identity.subject)
        if user is not None:
            if user.status != "active":
                raise InvalidAssertion("無効化された利用者")
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
        email = (identity.email or "").lower()
        domain = email.rpartition("@")[2]
        if not identity.email_verified or "@" not in email or domain not in allowed_domains():
            raise InvalidAssertion("登録が許可されていない利用者")
        return self.users.create_with_identity(
            identity.issuer, identity.subject, display_name=identity.name, email=identity.email
        )
