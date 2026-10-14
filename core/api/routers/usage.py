"""おいとまAPI の利用回数。"""

from fastapi import APIRouter

from core.api.dependencies import StoreDep, UserDep
from core.store import DailyUsage, today_jst

router = APIRouter()


@router.get("/usage/today")
def usage_today(store: StoreDep, user: UserDep) -> DailyUsage:
    """自分の今日（日本時間）の おいとまAPI の利用回数とトークン数。解析中の分は約10秒ごとに加わる。"""
    return store.get_daily_usage(today_jst(), user_id=user.user_id)
