"""解析のデータモデル。LLM の入出力スキーマと、指摘（SPEC 3.4）。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.ingest.models import BBox

Severity = Literal["critical", "major", "suggestion"]
Category = Literal["logic", "readability", "rule", "citation", "lab"]

# 段落内チェックの種類（SPEC 3.3-3）
ParagraphIssueType = Literal[
    "premise_evidence_mismatch",  # 前提と根拠の不一致
    "leap",  # 論理の飛躍
    "overclaim",  # 主張の過大（根拠に対して強すぎる表現）
    "subject_predicate_mismatch",  # 主語と述語のねじれ
    "ambiguous_reference",  # 指示語の曖昧さ
]
# 段落間チェックの種類
FlowIssueType = Literal[
    "disconnection",  # 段落間の接続の断絶
    "section_role_violation",  # セクションの役割違反
]

CATEGORY_OF: dict[str, Category] = {
    "premise_evidence_mismatch": "logic",
    "leap": "logic",
    "overclaim": "logic",
    "disconnection": "logic",
    "subject_predicate_mismatch": "readability",
    "ambiguous_reference": "readability",
    "section_role_violation": "readability",
}

SEVERITY_RANK: dict[str, int] = {"critical": 0, "major": 1, "suggestion": 2}


class _LLMOutput(BaseModel):
    """LLM の出力。余分なキーは無視し、必要なキーだけ検証する。"""

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------- 論証構造（SPEC 3.3-2）


class ArgumentUnit(_LLMOutput):
    sentence_ids: list[str]
    text: str


class ArgumentStructure(_LLMOutput):
    claims: list[ArgumentUnit] = Field(default_factory=list)
    premises: list[ArgumentUnit] = Field(default_factory=list)
    evidence: list[ArgumentUnit] = Field(default_factory=list)
    connectives: list[str] = Field(default_factory=list)
    novelty_claim: bool = False
    summary: str


class ParagraphStructure(ArgumentStructure):
    paragraph_id: str


# ---------------------------------------------------------------- チェックの出力


class ParagraphIssue(_LLMOutput):
    type: ParagraphIssueType
    sentence_ids: list[str] = Field(min_length=1)
    quote: str
    issue: str
    rationale: str
    severity: Severity
    # 問題の判断の根拠になった文（前提・根拠として使われている文など）
    related_sentence_ids: list[str] = Field(default_factory=list)
    # 文が途中で途切れている・未完であることを理由にした指摘か（true のものは捨てる）
    about_truncation: bool = False


class ParagraphCheckOutput(_LLMOutput):
    issues: list[ParagraphIssue] = Field(max_length=10)


class FlowIssue(_LLMOutput):
    type: FlowIssueType
    paragraph_id: str
    previous_paragraph_id: str | None = None
    issue: str
    rationale: str
    severity: Severity


class FlowCheckOutput(_LLMOutput):
    issues: list[FlowIssue] = Field(max_length=20)


class SuggestionOutput(_LLMOutput):
    suggestions: list[str] = Field(min_length=1, max_length=2)


# ---------------------------------------------------------------- 指摘（SPEC 3.4）


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # manuscript：原稿内の文（論理チェックで前提・根拠として参照した文）
    type: Literal["rule", "paper", "lab", "external", "manuscript"]
    ref: str
    excerpt: str


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: str
    secondary: str | None = None  # M9 の二重チェックで設定する
    agreed: bool | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    doc_id: str
    category: Category
    subcategory: str
    severity: Severity
    section_id: str | None = None
    page: int = Field(ge=1)
    # SPEC 3.4 の bboxes に、文がページをまたぐ場合に備えてページ番号を持たせたもの
    bboxes: list[BBox]
    sentence_ids: list[str]
    quote: str
    issue: str
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    # M9 で第2モデルとの一致により決める。それまでは自己一貫性の判定が全会一致なら high
    confidence: Literal["high", "low"]
    votes: int = Field(ge=1)  # 自己一貫性で同じ指摘が出た回数
    samples: int = Field(ge=1)  # 判定の回数
    models: ModelInfo
    status: Literal["open", "accepted", "dismissed"] = "open"
    dismiss_reason: str | None = None

    @property
    def voted(self) -> bool:
        """多数決（複数回の判定）を取った指摘か。1回だけの判定の指摘には、確度と回数を示さない。"""
        return self.samples >= 2
