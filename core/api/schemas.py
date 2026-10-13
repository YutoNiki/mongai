"""API の入出力（Pydantic）。"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from core.analyze.context import OutputLang
from core.analyze.models import Finding
from core.llm.usage import UsageRow
from core.rules.models import RuleStatus
from core.store import AnalysisRecord, PaperRecord, Progress


class UploadResponse(BaseModel):
    doc_id: str
    filename: str
    size: int


class CreateAnalysisRequest(BaseModel):
    doc_id: str
    output_lang: OutputLang = "paper"


class StatusUpdate(BaseModel):
    status: Literal["open", "accepted", "dismissed"]
    reason: str | None = Field(default=None, max_length=1000)


class DocumentInfo(BaseModel):
    title: str | None
    lang: Literal["ja", "en"]
    page_count: int


class SectionCount(BaseModel):
    """セクション別の件数（論文内の順。小見出しは大見出しにまとめる）。"""

    key: str
    label: str
    count: int


class Counts(BaseModel):
    """却下した指摘を除いた件数。"""

    total: int
    by_severity: dict[str, int]
    by_category: dict[str, int]
    by_section: list[SectionCount]
    # セクションID → by_section の key（小見出しを大見出しにまとめる対応表。絞り込みに使う）
    section_of: dict[str, str]
    by_status: dict[str, int]
    by_confidence: dict[str, int]


class PreviousAnalysis(BaseModel):
    analysis_id: str
    created_at: str
    total: int
    by_severity: dict[str, int]


class PersistingPair(BaseModel):
    finding_id: str  # 今回の指摘
    previous_finding_id: str  # 対応する前回の指摘


class AnalysisDiff(BaseModel):
    """前回の解析との差分（SPEC 3.5）。前回が無ければ previous は None で、ほかは空。"""

    previous: PreviousAnalysis | None
    resolved: list[Finding]  # 前回はあったが、今回は無い指摘（前回の内容）
    new: list[str]  # 今回だけにある指摘の番号
    persisting: list[PersistingPair]


class AnalysisSummary(BaseModel):
    analysis: AnalysisRecord
    paper: PaperRecord | None
    document: DocumentInfo | None
    counts: Counts
    previous: PreviousAnalysis | None
    models: dict[str, str]
    api_usage: list[UsageRow]
    api_requests: int
    external_requests: int  # 学外への要求の件数（M7 の検索ゲートウェイで記録する）


class AnalysisListItem(BaseModel):
    analysis_id: str
    doc_id: str
    title: str | None
    filename: str | None
    status: str
    created_at: str
    finished_at: str | None
    total: int
    progress: Progress = Progress()  # トップページの進捗バー


class AuditLog(BaseModel):
    available: bool
    mode: str | None = None
    entries: list[dict[str, Any]]


class BulkRuleStatus(BaseModel):
    """複数のルールのステータスをまとめて変える。"""

    rule_ids: list[str] = Field(min_length=1, max_length=1000)
    status: RuleStatus
