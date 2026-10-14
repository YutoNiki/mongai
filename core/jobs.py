"""解析ジョブ（worker で実行する）：構造化 → 論理・読みやすさの解析 → 保存。

進捗と、確定した指摘を順に保存するので、画面は解析中でも結果を表示できる。

ジョブの引数は ID だけで、所有者はデータベースの記録から決める（Redis の内容は信用しない）。
解析の材料（ルール・過去の指摘）と外部への要求・利用回数は、すべてその所有者のものにする。
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
from core.lab.extract import extract_lab_knowledge
from core.lab.store import LabStore
from core.llm.client import OitomaClient
from core.rules.extract import extract_rules
from core.rules.store import RuleStore
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
    rule_store: RuleStore | None = None,
    lab_store: LabStore | None = None,
) -> str:
    """解析を1件実行し、最終的なステータスを返す。store・client はテスト用に差し替えられる。"""
    store = store or Store()
    rule_store = rule_store or RuleStore(store.path)
    lab_store = lab_store or LabStore(store.path)
    record = store.get_analysis(analysis_id)
    if record is None:
        raise ValueError(f"解析 {analysis_id} が見つかりません")
    owner = record.owner_user_id
    if record.visibility != "private" or owner is None:
        # 共通デモ・所有者の分からない解析は実行せず、記録も変えない（API でも積まないが、二重に防ぐ）
        logger.warning("解析 %s は所有者のいる解析ではないため、実行しません", analysis_id)
        return "failed"
    settings = AnalysisSettings.model_validate(record.settings)
    own_client = client is None
    llm = client or OitomaClient()
    gw = (gateway or GatewayClient()).for_owner(owner)
    store.set_status(analysis_id, "running")
    flushed_at = time.monotonic()

    def flush_daily_usage(force: bool = False) -> None:
        nonlocal flushed_at
        if not force and time.monotonic() - flushed_at < DAILY_USAGE_INTERVAL_S:
            return
        flushed_at = time.monotonic()
        store.add_daily_usage(today_jst(), llm.usage.unreported(), user_id=owner)

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

        # 所有者の承認済みのルール（解析の開始時点のもの）を使い、その件数と更新日時を記録する
        rules = rule_store.list_rules(owner, status="approved")
        approved, updated_at = rule_store.ruleset_version(owner)
        store.update_settings(
            analysis_id, {"ruleset": {"approved": approved, "updated_at": updated_at}}
        )
        result = await analyze_logic(
            doc,
            llm,
            settings,
            on_progress=on_progress,
            on_findings=on_findings,
            gateway=gw,
            analysis_id=analysis_id,
            rules=rules,
            rule_vectors=rule_store.embeddings(owner, status="approved"),
            # 所有者の過去の指摘だけを使う（ほかの利用者のものは参照しない）
            lab_comments=lab_store.list_comments(owner),
            lab_vectors=lab_store.comment_vectors(owner),
        )
        store.save_structures(analysis_id, result.structures)
        store.save_term_lookups(analysis_id, result.terms)
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


async def run_rule_extraction(
    ctx: dict[str, Any],
    document_id: str,
    *,
    rule_store: RuleStore | None = None,
    client: OitomaClient | None = None,
) -> str:
    """ルール資料からルールを抽出して保存し（保留の状態）、最終的なステータスを返す。"""
    rule_store = rule_store or RuleStore()
    owner = rule_store.document_owner(document_id)
    own_client = client is None
    llm = client or OitomaClient()
    rule_store.set_document_status(document_id, "running")
    try:
        added = await extract_rules(llm, rule_store, document_id)
        rule_store.set_document_status(document_id, "completed")
        logger.info("ルール資料 %s から %d 件のルールを追加しました", document_id, added)
        return "completed"
    except Exception as exc:
        logger.exception("ルール資料 %s の抽出に失敗しました", document_id)
        rule_store.set_document_status(
            document_id, "failed", error=f"ルールの抽出に失敗しました（{type(exc).__name__}）"
        )
        return "failed"
    finally:
        with contextlib.suppress(Exception):
            Store(rule_store.path).add_daily_usage(
                today_jst(), llm.usage.unreported(), user_id=owner
            )
        if own_client:
            await llm.aclose()


async def run_lab_extraction(
    ctx: dict[str, Any],
    document_id: str,
    *,
    lab_store: LabStore | None = None,
    rule_store: RuleStore | None = None,
    client: OitomaClient | None = None,
) -> str:
    """専門知識の資料から過去の指摘と研究室のルールを取り出して保存し、最終的なステータスを返す。"""
    lab_store = lab_store or LabStore()
    rule_store = rule_store or RuleStore(lab_store.path)
    found = lab_store.find_document(document_id)
    owner = found[1] if found else None
    own_client = client is None
    llm = client or OitomaClient()
    lab_store.set_document_status(document_id, "running")
    try:
        comments, rules = await extract_lab_knowledge(llm, lab_store, rule_store, document_id)
        lab_store.set_document_status(document_id, "completed", rule_count=rules)
        logger.info(
            "専門知識 %s から指摘 %d 件・ルール %d 件を取り出しました",
            document_id,
            comments,
            rules,
        )
        return "completed"
    except Exception as exc:
        logger.exception("専門知識 %s の取り込みに失敗しました", document_id)
        lab_store.set_document_status(
            document_id, "failed", error=f"取り込みに失敗しました（{type(exc).__name__}）"
        )
        return "failed"
    finally:
        with contextlib.suppress(Exception):
            Store(lab_store.path).add_daily_usage(
                today_jst(), llm.usage.unreported(), user_id=owner
            )
        if own_client:
            await llm.aclose()
