"""複数のルーターが使う、主体が読める・変更できるリソースの読み出し。

- 読めないもの（存在しない・他人のもの・隔離されたもの）は区別せず 404
- 読めるが変更できないもの（共通デモ）は 403
"""

from typing import Literal

from fastapi import HTTPException

from core.analyze.models import Finding
from core.api.schemas import PreviousAnalysis
from core.api.summary import count_findings
from core.auth.principal import AuthenticatedUser, Principal, viewer_of
from core.report.diff import FindingsDiff, diff_findings
from core.sample import is_sample
from core.store import AnalysisRecord, PaperRecord, Store

Severity = Literal["critical", "major", "suggestion"]
Category = Literal["logic", "readability", "rule", "citation", "lab"]
Status = Literal["open", "accepted", "dismissed"]
Confidence = Literal["high", "low"]


def is_demo(record: AnalysisRecord) -> bool:
    """共通デモの解析（公開データ、またはデモの論文に付いた解析）。誰も変更できない。"""
    return record.visibility == "public_demo" or is_sample(record.doc_id)


def readable_analysis(store: Store, principal: Principal, analysis_id: str) -> AnalysisRecord:
    """主体が読める解析（自分の解析か共通デモ）。それ以外は 404。"""
    record = store.get_analysis_for(analysis_id, viewer_of(principal))
    if record is None:
        raise HTTPException(status_code=404, detail="解析が見つかりません")
    return record


def owned_analysis(store: Store, user: AuthenticatedUser, analysis_id: str) -> AnalysisRecord:
    """主体が変更できる解析。読めなければ 404、共通デモなら 403。"""
    record = readable_analysis(store, user, analysis_id)
    if record.visibility != "private" or is_demo(record):
        raise HTTPException(status_code=403, detail="デモの解析は変更できません")
    return record


def readable_paper(store: Store, principal: Principal, doc_id: str) -> PaperRecord:
    """主体が読める論文（自分の論文か共通デモ）。それ以外は 404。"""
    paper = store.get_paper_for(doc_id, viewer_of(principal))
    if paper is None:
        raise HTTPException(status_code=404, detail="論文が見つかりません")
    return paper


def owned_paper(store: Store, user: AuthenticatedUser, doc_id: str) -> PaperRecord:
    """主体が変更できる論文。読めなければ 404、共通デモなら 403。"""
    paper = readable_paper(store, user, doc_id)
    if paper.visibility != "private" or is_sample(doc_id):
        raise HTTPException(status_code=403, detail="デモの論文は変更できません")
    return paper


def filtered_findings(
    store: Store,
    record: AnalysisRecord,
    category: Category | None,
    severity: Severity | None,
    section_id: str | None,
    confidence: Confidence | None,
    status: Status | None,
) -> list[Finding]:
    """読めることを確かめた解析（readable_analysis の結果）の指摘を絞り込む。"""
    findings = store.get_findings(
        record.analysis_id,
        category=category,
        severity=severity,
        section_id=section_id,
        confidence=confidence,
        status=status,
    )
    # 確度で絞るときは、確度を示さない（多数決をしていない）指摘を除く
    return [f for f in findings if f.voted] if confidence else findings


def previous_and_diff(
    store: Store, record: AnalysisRecord
) -> tuple[PreviousAnalysis | None, FindingsDiff | None]:
    """同じ所有者の、同じ論文の前回の完了した解析と、その差分（前回が無ければ None）。"""
    prev_record = store.find_previous(record.analysis_id)
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
    return previous, diff_findings(store.get_findings(record.analysis_id), previous_findings)
