"""デモの解析：デモ用の合成論文（demo/demo_paper_ja.pdf）の解析結果を、最初から一覧に表示する。

解析結果は 2026-10-14 に実際に解析したもの（サービス名を SI-NSA に改めた版のデモ論文）。
合成データなので未発表の論文は含まない。
ワーカーの起動時に、無ければ取り込む。デモの論文は削除できない（画面では「（デモ）」と表示する）。
"""

import json
import logging
from pathlib import Path

from pydantic import BaseModel

from core.analyze.models import Finding
from core.ingest.models import Document
from core.llm.usage import UsageRow
from core.store import AnalysisRecord, Store
from core.store.files import doc_id_for, save_paper

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
    pdf = (SAMPLE_DIR / "paper.pdf").read_bytes()
    if doc_id_for(pdf) != SAMPLE_DOC_ID:
        raise ValueError("デモの論文の PDF が、デモの解析結果と一致しません")
    meta = _SampleAnalysis.model_validate_json((SAMPLE_DIR / "analysis.json").read_text())
    document = Document.model_validate_json((SAMPLE_DIR / "document.json").read_text())
    findings = [
        Finding.model_validate(f)
        for f in json.loads((SAMPLE_DIR / "findings.json").read_text(encoding="utf-8"))
    ]
    save_paper(pdf)
    store.save_paper(
        SAMPLE_DOC_ID, filename=meta.filename, size=len(pdf), uploaded_at=meta.uploaded_at
    )
    store.save_document(document)
    store.import_analysis(meta.analysis, findings, meta.api_usage)
    logger.info("デモの解析 %s を取り込みました", SAMPLE_ANALYSIS_ID)
    return True
