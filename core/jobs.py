"""解析ジョブ（worker で実行する）：構造化 → 論理・読みやすさの解析 → 保存。

進捗と、確定した指摘を順に保存するので、画面は解析中でも結果を表示できる。
"""

import contextlib
import logging
import time
from typing import Any

from core.analyze.models import Finding
from core.analyze.pipeline import AnalysisSettings, analyze_logic
from core.annotate.locate import relocate_findings, summary
from core.gateway_client import GatewayClient, GatewayError
from core.ingest.pipeline import ingest_pdf
from core.llm.client import OitomaClient
from core.store import Store, today_jst
from core.store.files import paper_path

logger = logging.getLogger(__name__)

# 日ごとのAPI利用回数を保存する間隔（秒）。トップページの「今日の利用」が解析中も増えるようにする
DAILY_USAGE_INTERVAL_S = 10.0


async def run_analysis(
    ctx: dict[str, Any],
    analysis_id: str,
    *,
    store: Store | None = None,
    client: OitomaClient | None = None,
    gateway: GatewayClient | None = None,
) -> str:
    """解析を1件実行し、最終的なステータスを返す。store・client はテスト用に差し替えられる。"""
    store = store or Store()
    record = store.get_analysis(analysis_id)
    if record is None:
        raise ValueError(f"解析 {analysis_id} が見つかりません")
    settings = AnalysisSettings.model_validate(record.settings)
    own_client = client is None
    llm = client or OitomaClient()
    gw = gateway or GatewayClient()
    store.set_status(analysis_id, "running")
    flushed_at = time.monotonic()

    def flush_daily_usage(force: bool = False) -> None:
        nonlocal flushed_at
        if not force and time.monotonic() - flushed_at < DAILY_USAGE_INTERVAL_S:
            return
        flushed_at = time.monotonic()
        store.add_daily_usage(today_jst(), llm.usage.unreported())

    try:
        store.set_progress(analysis_id, "ingest", 0, 1)
        doc = await ingest_pdf(paper_path(record.doc_id), doc_id=record.doc_id, llm=llm)
        store.save_document(doc)
        store.set_progress(analysis_id, "ingest", 1, 1)

        def on_progress(stage: str, done: int, total: int) -> None:
            store.set_progress(analysis_id, stage, done, total)
            flush_daily_usage()

        def on_findings(findings: list[Finding]) -> None:
            store.save_findings(analysis_id, findings)

        result = await analyze_logic(
            doc,
            llm,
            settings,
            on_progress=on_progress,
            on_findings=on_findings,
            gateway=gw,
            analysis_id=analysis_id,
        )
        store.save_structures(analysis_id, result.structures)
        # 指摘の位置を PDF 上の引用箇所に合わせる（文の座標がずれる PDF への対策）
        store.set_progress(analysis_id, "locate", 0, len(result.findings))
        findings, counts = relocate_findings(
            paper_path(record.doc_id).read_bytes(), result.findings
        )
        located = summary(counts)
        store.save_findings(analysis_id, findings)  # 改善案・位置を更新したもので保存する
        store.finish_analysis(
            analysis_id,
            status="completed",
            warnings=doc.warnings + result.warnings + ([located] if located else []),
            api_usage=llm.usage.rows(),
        )
        llm.usage.log_summary(label=analysis_id)
        return "completed"
    except Exception as exc:
        # 例外の内容に論文の本文が含まれる可能性は低いが、利用者に見せるのは種類だけにする
        logger.exception("解析 %s に失敗しました", analysis_id)
        store.finish_analysis(
            analysis_id,
            status="failed",
            error=f"解析に失敗しました（{type(exc).__name__}）",
            api_usage=llm.usage.rows(),
        )
        return "failed"
    finally:
        with contextlib.suppress(Exception):  # 日ごとの集計の失敗で、解析の結果を変えない
            flush_daily_usage(force=True)
        # 原稿の照合情報を gateway のメモリから消す（承認後の再解析では、改めて登録する）
        with contextlib.suppress(GatewayError):
            await gw.forget(record.doc_id)
        if own_client:
            await llm.aclose()
