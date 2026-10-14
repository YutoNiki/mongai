"""ログインの状態（画面の表示用）。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.api.dependencies import PrincipalDep, get_user_directory
from core.auth.directory import UserDirectory
from core.auth.principal import AuthenticatedUser

router = APIRouter()


class Me(BaseModel):
    """ゲストか、ログインした利用者か。表示に要る最小限の情報だけを返す（内部ユーザーID・外部の識別子は返さない）。"""

    authenticated: bool
    email: str | None = None
    display_name: str | None = None


@router.get("/me")
def me(
    principal: PrincipalDep,
    directory: Annotated[UserDirectory, Depends(get_user_directory)],
) -> Me:
    """主張が無ければゲスト。主張が無効なら 401（invalid_session。get_principal で判定する）。"""
    if not isinstance(principal, AuthenticatedUser):
        return Me(authenticated=False)
    user = directory.users.get_user(principal.user_id)
    return Me(
        authenticated=True,
        email=user.email if user else None,
        display_name=user.display_name if user else None,
    )
