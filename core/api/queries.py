"""複数のルーターが使う、解析と指摘の読み出し（見つからなければ 404）。"""

from typing import Literal

from fastapi import HTTPException

from core.analyze.models import Finding
from core.api.schemas import PreviousAnalysis
from core.api.summary import count_findings
from core.report.diff import FindingsDiff, diff_findings
from core.store import AnalysisRecord, Store

Severity = Literal["critical", "major", "suggestion"]
Category = Literal["logic", "readability", "rule", "citation", "lab"]
Status = Literal["open", "accepted", "dismissed"]
Confidence = Literal["high", "low"]


def analysis_or_404(store: Store, analysis_id: str) -> AnalysisRecord:
    record = store.get_analysis(analysis_id)
    if record is None:
        raise HTTPException(status_code=404, detail="解析が見つかりません")
    return record


def filtered_findings(
    store: Store,
    analysis_id: str,
    category: Category | None,
    severity: Severity | None,
    section_id: str | None,
    confidence: Confidence | None,
    status: Status | None,
) -> list[Finding]:
    analysis_or_404(store, analysis_id)
    findings = store.get_findings(
        analysis_id,
        category=category,
        severity=severity,
        section_id=section_id,
        confidence=confidence,
        status=status,
    )
    # 確度で絞るときは、確度を示さない（多数決をしていない）指摘を除く
    return [f for f in findings if f.voted] if confidence else findings


def previous_and_diff(
    store: Store, analysis_id: str
) -> tuple[PreviousAnalysis | None, FindingsDiff | None]:
    """同じ論文の前回の完了した解析と、その差分（前回が無ければ None）。"""
    prev_record = store.find_previous(analysis_id)
    if prev_record is None:
        return None, None
    previous_findings = store.get_findings(prev_record.analysis_id)
    prev_counts = count_findings(previous_findings, None)
    previous = PreviousAnalysis(
        analysis_id=prev_record.analysis_id,
        created_at=prev_record.created_at,
        total=prev_counts.total,
        by_severity=prev_counts.by_severity,
    )
    return previous, diff_findings(store.get_findings(analysis_id), previous_findings)
