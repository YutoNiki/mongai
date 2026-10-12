"""解析ジョブのワーカー（arq）。core と同じイメージで、起動コマンドだけ変える。"""

import logging
import os
from typing import Any, ClassVar

from arq.connections import RedisSettings

from core.jobs import run_analysis
from core.logging_config import configure_logging
from core.store import Store

configure_logging()


logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    """中断された解析の後始末。ジョブは再実行しない設定のため、実行中だったものは失敗にする。"""
    failed, queued = Store().recover_interrupted()
    for analysis_id in failed:
        logger.warning("解析 %s はワーカーの再起動で中断されたため、失敗にしました", analysis_id)
    for analysis_id in queued:
        # Redis が再起動して順番待ちのジョブが消えた場合に備えて積み直す（同じ ID のジョブは重複しない）
        await ctx["redis"].enqueue_job("run_analysis", analysis_id, _job_id=analysis_id)


async def ping(ctx: dict[str, Any]) -> str:
    """疎通確認用のジョブ。"""
    return "pong"


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping, run_analysis]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    # `arq --check` による healthcheck のため短めにする
    health_check_interval = 30
    # 論文1本の解析は数十分かかることがある
    job_timeout = 3 * 60 * 60
    max_jobs = 2
    # 失敗したジョブを自動で再実行しない（おいとまAPIへの要求が重複するため）
    max_tries = 1
