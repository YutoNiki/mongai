"""ログの設定。core のサービス・CLI の起動時に呼ぶ。

ログは標準出力へ出す（docker compose logs で確認できる）。プロンプトや論文の本文はログに出さない。
"""

import logging
import os


def configure_logging() -> None:
    level = os.environ.get("MONGAI_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx は URL などをログに出すため、警告以上だけにする
    logging.getLogger("httpx").setLevel(logging.WARNING)
