"""解析ジョブのワーカー（arq）。core と同じイメージで、起動コマンドだけ変える。"""

import os
from typing import Any, ClassVar

from arq.connections import RedisSettings

from core.jobs import run_analysis
from core.logging_config import configure_logging

configure_logging()


async def ping(ctx: dict[str, Any]) -> str:
    """疎通確認用のジョブ。"""
    return "pong"


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping, run_analysis]
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    # `arq --check` による healthcheck のため短めにする
    health_check_interval = 30
    # 論文1本の解析は数十分かかることがある
    job_timeout = 3 * 60 * 60
    max_jobs = 2
    # 失敗したジョブを自動で再実行しない（おいとまAPIへの要求が重複するため）
    max_tries = 1
