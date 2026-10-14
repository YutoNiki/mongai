"""専門知識のデータモデル。資料と過去の指摘は利用者個人のもので、ほかの利用者のデータは参照しない。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 指摘の種類（過去の指摘を分類しておき、似た箇所の判定のときに伝える）
LabCategory = Literal["logic", "readability", "rule", "citation", "other"]


class LabSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc: str  # 資料のファイル名
    page: int | None = None


class LabComment(BaseModel):
    """過去の査読・添削での指摘（ほかの論文にも当てはまる形に一般化したもの）。"""

    model_config = ConfigDict(extra="forbid")

    comment_id: str
    document_id: str
    comment: str = Field(min_length=1, max_length=500)
    example: str | None = Field(default=None, max_length=500)  # 指摘された原稿の箇所（あれば）
    category: LabCategory = "other"
    source: LabSource
    created_at: str = ""


class LabDocument(BaseModel):
    document_id: str
    filename: str
    kind: Literal["pdf", "docx", "markdown", "text"]
    status: Literal["queued", "running", "completed", "failed"]
    comment_count: int = 0
    rule_count: int = 0
    error: str | None = None
    created_at: str
    finished_at: str | None = None
