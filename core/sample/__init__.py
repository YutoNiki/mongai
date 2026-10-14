"""デモの解析：デモ用の合成論文（demo/demo_paper_ja.pdf）の解析結果を、最初から一覧に表示する。

解析結果は 2026-10-14 に実際に解析したもの（サービス名を SI-NSA に改めた版のデモ論文）。
合成データなので未発表の論文は含まない。
ワーカーの起動時に、無ければ取り込む。デモは特定の利用者に所有させない公開データ（visibility が
public_demo）で、誰でも閲覧でき、誰も変更・削除できない（画面では「（デモ）」と表示する）。
"""

import json
import logging
from pathlib import Path

from pydantic import BaseModel

from core.analyze.models import Finding
from core.ingest.models import Document
from core.llm.usage import UsageRow
from core.store import AnalysisRecord, Store
from core.store.files import content_sha256, doc_id_for, save_paper

logger = logging.getLogger(__name__)

SAMPLE_DIR = Path(__file__).parent
SAMPLE_DOC_ID = "e43feb49a5520009"
SAMPLE_ANALYSIS_ID = "A-198ecfa60c54"


class _SampleAnalysis(BaseModel):
    analysis: AnalysisRecord
    filename: str
    uploaded_at: str
    api_usage: list[UsageRow]


def is_sample(doc_id: str) -> bool:
    return doc_id == SAMPLE_DOC_ID


def install_sample(store: Store) -> bool:
    """デモの解析が無ければ取り込み、取り込んだら True を返す。"""
    if store.get_analysis(SAMPLE_ANALYSIS_ID) is not None:
        return False
    existing = store.get_paper(SAMPLE_DOC_ID)
    if existing is not None and existing.visibility != "public_demo":
        # 認証の導入前に作られた行など。上書きせず、移行（make migrate）に任せる
        logger.warning("デモの論文の ID が公開データでない行に使われているため、取り込みません")
        return False
    pdf = (SAMPLE_DIR / "paper.pdf").read_bytes()
    if doc_id_for(pdf) != SAMPLE_DOC_ID:
        raise ValueError("デモの論文の PDF が、デモの解析結果と一致しません")
    meta = _SampleAnalysis.model_validate_json((SAMPLE_DIR / "analysis.json").read_text())
    document = Document.model_validate_json((SAMPLE_DIR / "document.json").read_text())
    findings = [
        Finding.model_validate(f)
        for f in json.loads((SAMPLE_DIR / "findings.json").read_text(encoding="utf-8"))
    ]
    save_paper(pdf, SAMPLE_DOC_ID)
    if existing is None:
        store.save_paper(
            SAMPLE_DOC_ID,
            owner_user_id=None,
            filename=meta.filename,
            size=len(pdf),
            content_sha256=content_sha256(pdf),
            uploaded_at=meta.uploaded_at,
            visibility="public_demo",
        )
    store.save_document(document)
    store.import_analysis(meta.analysis, findings, meta.api_usage)
    logger.info("デモの解析 %s を取り込みました", SAMPLE_ANALYSIS_ID)
    return True
