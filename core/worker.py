"""解析ジョブのワーカー（arq）。core と同じイメージで、起動コマンドだけ変える。"""

import os
from typing import Any, ClassVar

from arq.connections import RedisSettings

from core.logging_config import configure_logging

configure_logging()


async def ping(ctx: dict[str, Any]) -> str:
    """疎通確認用のジョブ。解析ジョブは M2 以降で追加する。"""
    return "pong"


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping]
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    # `arq --check` による healthcheck のため短めにする
    health_check_interval = 30
