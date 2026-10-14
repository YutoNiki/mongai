"""リクエストの主体（ゲストか、認証済みの利用者か）。

主体にはメールアドレスや外部の識別子を持たせない（認可の判定には内部ユーザーIDだけを使う）。
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Guest:
    kind: Literal["guest"] = "guest"


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str  # 内部ユーザーID（U-…）
    kind: Literal["user"] = "user"


Principal = Guest | AuthenticatedUser


def viewer_of(principal: Principal) -> str | None:
    """ストアの `*_for` に渡す主体（ゲストは None。共通デモだけが読める）。"""
    return principal.user_id if isinstance(principal, AuthenticatedUser) else None
