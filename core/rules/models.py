"""ルールのデータモデル（SPEC 3.2）。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.ingest.models import SectionRole

TargetSection = Literal[
    "Abstract", "Introduction", "Methods", "Results", "Discussion", "全体", "図表", "引用"
]
CheckType = Literal["mechanical", "llm", "advisory"]
RuleLang = Literal["ja", "en", "both"]
RuleStatus = Literal["pending", "approved", "disabled"]
# 機械判定（Python で判定する）の種類。抽出したルールは、このどれかに当てはまるときだけ mechanical になる
Checker = Literal["abbreviation_definition", "figure_table_reference", "unit_consistency"]

# ルールの対象セクション → 論文のセクションの役割
SECTION_ROLES: dict[str, frozenset[SectionRole]] = {
    "Abstract": frozenset({"abstract"}),
    "Introduction": frozenset({"introduction", "related_work"}),
    "Methods": frozenset({"methods"}),
    "Results": frozenset({"results", "experiments"}),
    "Discussion": frozenset({"discussion", "conclusion"}),
}


class RuleSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc: str  # 出典（資料のファイル名、または「組み込み」「研究室：過去査読」）
    page: int | None = None


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    target_section: TargetSection
    rule: str = Field(min_length=1, max_length=1000)
    check_type: CheckType
    checker: Checker | None = None  # check_type が mechanical のときの判定の種類
    lang: RuleLang = "both"
    source: RuleSource
    # 統合した言い換え・重複のルールの出典
    merged_sources: list[RuleSource] = Field(default_factory=list)
    field_tags: list[str] = Field(default_factory=list)
    status: RuleStatus = "pending"
    builtin: bool = False  # 組み込みのルール（削除はできず、無効にだけできる）
    document_id: str | None = None  # 抽出元の資料
    updated_at: str = ""


class RuleDocument(BaseModel):
    """アップロードされたルール資料。"""

    document_id: str
    filename: str
    kind: Literal["pdf", "markdown", "text"]
    status: Literal["queued", "running", "completed", "failed"]
    rule_count: int = 0
    error: str | None = None
    created_at: str
    finished_at: str | None = None


class RuleUpdate(BaseModel):
    """画面からの編集。指定した項目だけを変える。"""

    model_config = ConfigDict(extra="forbid")

    status: RuleStatus | None = None
    rule: str | None = Field(default=None, min_length=1, max_length=1000)
    target_section: TargetSection | None = None
    check_type: CheckType | None = None


# 組み込みの機械判定のルール（最初から承認済み。画面で無効にできる）
BUILTIN_RULES: list[Rule] = [
    Rule(
        rule_id="B-ABBR",
        target_section="全体",
        rule="略語は初出時に正式名称とともに定義する（例：長短期記憶（LSTM））",
        check_type="mechanical",
        checker="abbreviation_definition",
        source=RuleSource(doc="組み込み"),
        status="approved",
        builtin=True,
    ),
    Rule(
        rule_id="B-FIGREF",
        target_section="図表",
        rule="すべての図表を本文中で参照する（参照されない図表や、存在しない図表への参照をなくす）",
        check_type="mechanical",
        checker="figure_table_reference",
        source=RuleSource(doc="組み込み"),
        status="approved",
        builtin=True,
    ),
    Rule(
        rule_id="B-UNIT",
        target_section="全体",
        rule="同じ単位は論文全体で同じ表記にする（例：「%」と「％」、「ms」と「ミリ秒」を混在させない）",
        check_type="mechanical",
        checker="unit_consistency",
        source=RuleSource(doc="組み込み"),
        status="approved",
        builtin=True,
    ),
]
