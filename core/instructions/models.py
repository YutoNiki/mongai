"""課題・提出要件の入出力と解析時の固定条件。"""

import uuid
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.rules.models import Rule

DocumentType = Literal["paper", "course_report", "lab_report"]
ProfileScope = Literal["faculty", "course"]
CheckKind = Literal["content", "character_count", "reference_count", "heading", "style", "manual"]
Verdict = Literal["met", "not_met", "unknown"]

DOCUMENT_LABELS: dict[DocumentType, str] = {
    "paper": "論文",
    "course_report": "授業レポート",
    "lab_report": "実験レポート",
}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requirement(Model):
    requirement_id: str = Field(default_factory=lambda: f"REQ-{uuid.uuid4().hex[:12]}")
    text: str = Field(min_length=1, max_length=1000)
    source: str = Field(default="今回の課題", max_length=200)
    source_excerpt: str = Field(default="", max_length=2000)
    strength: Literal["required", "recommended"] = "required"
    kind: CheckKind = "content"
    minimum: int | None = Field(default=None, ge=0, le=1000000)
    maximum: int | None = Field(default=None, ge=0, le=1000000)
    value: str | None = Field(default=None, max_length=200)
    include_spaces: bool = False
    count_scope: Literal["body", "all"] = "body"
    aggregation: Literal["all", "any"] = "all"
    enabled: bool = True

    @model_validator(mode="after")
    def check_parameters(self) -> Self:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("下限は上限以下にしてください")
        if (
            self.kind in ("character_count", "reference_count")
            and self.minimum is None
            and self.maximum is None
        ):
            raise ValueError("件数・文字数の上限または下限を指定してください")
        if self.kind in ("heading", "style") and not self.value:
            raise ValueError("見出し名または文体を指定してください")
        return self


class Profile(Model):
    profile_id: str
    name: str
    scope: ProfileScope
    raw_text: str
    requirements: list[Requirement]
    revision: int
    updated_at: str


class ProfileRef(Model):
    profile_id: str
    revision: int = Field(ge=1)


class Conflict(Model):
    requirement_ids: list[str]
    reason: str


class Draft(Model):
    draft_id: str
    document_type: DocumentType = "paper"
    raw_text: str = ""
    profiles: list[Profile] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list, max_length=50)
    conflicts: list[Conflict] = Field(default_factory=list)
    status: Literal["queued", "running", "ready", "failed"] = "queued"
    error: str | None = None
    revision: int = 1
    confirmed: bool = False
    legacy_rules: list[Rule] = Field(default_factory=list)
    excluded_rule_ids: list[str] = Field(default_factory=list)


class Snapshot(Model):
    schema_version: Literal[1] = 1
    draft_id: str | None = None
    document_type: DocumentType
    profiles: list[Profile] = Field(default_factory=list)
    raw_text: str = ""
    requirements: list[Requirement] = Field(default_factory=list, max_length=50)
    excluded_rule_ids: list[str] = Field(default_factory=list)
    legacy_rules: list[Rule] = Field(default_factory=list)


class RequirementResult(Model):
    requirement_id: str
    verdict: Verdict = "unknown"
    reason: str
    sentence_ids: list[str] = Field(default_factory=list)
    paragraph_ids: list[str] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)
    quote: str = ""
    coverage: Literal["full", "partial", "none"] = "none"
    finding_id: str | None = None
    state: Literal["pending", "completed"] = "completed"
    suggestion: str = ""


class RequirementsResponse(Model):
    snapshot: Snapshot | None
    results: list[RequirementResult]


def detect_conflicts(requirements: list[Requirement]) -> list[Conflict]:
    """機械判定できる競合を検出する。必須条件だけを解析開始の妨げにする。"""
    conflicts: list[Conflict] = []
    active = [r for r in requirements if r.enabled and r.strength == "required"]
    for i, a in enumerate(active):
        for b in active[i + 1 :]:
            if a.kind != b.kind:
                continue
            incompatible = False
            if a.kind in ("character_count", "reference_count"):
                if a.kind == "character_count" and (a.count_scope, a.include_spaces) != (
                    b.count_scope,
                    b.include_spaces,
                ):
                    continue
                lower = max(a.minimum or 0, b.minimum or 0)
                upper = min(
                    a.maximum if a.maximum is not None else 1000001,
                    b.maximum if b.maximum is not None else 1000001,
                )
                incompatible = lower > upper
            elif a.kind == "style":
                incompatible = {a.value, b.value} == {"である", "ですます"}
            if incompatible:
                conflicts.append(
                    Conflict(
                        requirement_ids=[a.requirement_id, b.requirement_id],
                        reason=f"両立しない条件です：「{a.text}」「{b.text}」。使う条件を編集・除外してください。",
                    )
                )
    return conflicts
