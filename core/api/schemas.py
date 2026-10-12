"""API の入出力（Pydantic）。"""

from typing import Literal

from pydantic import BaseModel, Field

from core.analyze.context import OutputLang
from core.llm.usage import UsageRow
from core.store import AnalysisRecord, PaperRecord


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


class AuditLog(BaseModel):
    available: bool
    entries: list[dict[str, str]]
