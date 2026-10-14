"""検索ゲートウェイを通れる要求と、検索結果のスキーマ（SPEC 2.3・2.4）。

学外に出せるのは、ここで定義した3種類の要求だけ。未定義のフィールドは拒否する。
用語の照会（TermLookup）は、文や記号列ではなく1語（複合語可）であることを、スキーマの段階で確かめる。
"""

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[^\s\"<>]{1,200}$")
_SENTENCE_PUNCT = re.compile(r"[。．、，,;；:：!?！？\n\r\t]|\.\s")
# 日本語の文らしさ：格助詞・係助詞（を・は・が・に・で・へ）や、文末の活用語尾を含む
_JA_PARTICLE = re.compile(r"[をはがにでへ]|から|まで|より|ます|です|ない|する|した|される")
_WORD_CHAR = re.compile(r"[A-Za-z0-9぀-ヿ㐀-䶿一-鿿]")
_ALLOWED_CHARS = re.compile(r"^[A-Za-z0-9぀-ヿ㐀-䶿一-鿿０-ｚ ・\-_/+.'’()（）]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


def _valid_doi(value: str) -> str:
    if not DOI_PATTERN.fullmatch(value):
        raise ValueError("DOI の形式が不正です")
    return value


class BibLookup(_Strict):
    """引用文献の書誌照合。"""

    kind: Literal["bib_lookup"]
    doi: str | None = None
    title: str | None = Field(default=None, max_length=300)  # 引用文献のタイトル（公開済み情報）
    first_author: str | None = Field(default=None, max_length=100)
    year: int | None = Field(default=None, ge=1800, le=2100)

    @field_validator("doi")
    @classmethod
    def _doi(cls, v: str | None) -> str | None:
        return _valid_doi(v) if v else None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        if v and "\n" in v:
            raise ValueError("タイトルに改行は使えません")
        return v or None

    @model_validator(mode="after")
    def _needs_doi_or_title(self) -> Self:
        if not self.doi and not self.title:
            raise ValueError("doi か title のどちらかが必要です")
        return self


class OATextFetch(_Strict):
    """引用文献のアブストラクト（OA本文）の取得。"""

    kind: Literal["oa_fetch"]
    doi: str

    @field_validator("doi")
    @classmethod
    def _doi(cls, v: str) -> str:
        return _valid_doi(v)


class TermLookup(_Strict):
    """一般用語の単独照会。1語（複合語可）、最大40文字。"""

    kind: Literal["term_lookup"]
    term: str = Field(min_length=1, max_length=40)
    lang: Literal["ja", "en"]

    @field_validator("term")
    @classmethod
    def _term(cls, v: str) -> str:
        if len(v.split()) >= 3:
            raise ValueError("用語は1語（複合語は2語まで）にしてください")
        if _SENTENCE_PUNCT.search(v):
            raise ValueError("文ではなく用語を指定してください")
        if not _WORD_CHAR.search(v) or not _ALLOWED_CHARS.fullmatch(v):
            raise ValueError("記号列は照会できません")
        hiragana = sum(1 for ch in v if "぀" <= ch <= "ゟ")
        if _JA_PARTICLE.search(v) and hiragana >= 2:
            raise ValueError("助詞を含む文は照会できません")
        return v


ExternalRequest = Annotated[BibLookup | OATextFetch | TermLookup, Field(discriminator="kind")]


OWNER_ID_PATTERN = r"^U-[0-9a-f]{32}$"


class GatewayRequest(_Strict):
    """core → gateway の要求。どの利用者・解析・論文の要求かを付けて送る（監査・承認・件数制限のため）。

    owner_id は内部ユーザーID（学内の gateway だけが使い、searcher には渡さない）。
    """

    owner_id: str = Field(pattern=OWNER_ID_PATTERN)
    analysis_id: str = Field(pattern=r"^A-[0-9a-f]{12}$")
    doc_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    request: ExternalRequest


class BibResult(BaseModel):
    """searcher の出力（SPEC 2.4）。外部ページの内容はデータとして扱う。"""

    model_config = ConfigDict(extra="forbid")

    found: bool
    doi: str | None = None
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    source: str  # どのAPIから得たか
    abstract: str | None = None
    oa_text_path: str | None = None  # 本文の取得は行わない（許可リスト外に転送されるため）


class TermResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    description: str | None = None
    source: str


class SearchResponse(BaseModel):
    """searcher → gateway の応答。"""

    model_config = ConfigDict(extra="forbid")

    results: list[BibResult] = Field(default_factory=list)
    term: TermResult | None = None
    errors: list[str] = Field(default_factory=list)


Decision = Literal["sent", "pending", "approved", "rejected", "blocked", "cached", "failed"]


class GatewayResponse(BaseModel):
    """gateway → core の応答。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    decision: Decision
    reason: str | None = None
    response: SearchResponse | None = None
