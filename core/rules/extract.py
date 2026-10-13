"""ルール資料からのルールの抽出と、重複・言い換えの統合（SPEC 3.2 の 1・2）。

1. 資料（PDF / Markdown / テキスト）の本文をページの印（[p.N]）付きで取り出し、チャンクに分ける
2. 高速モデル（役割 rule_extraction）でチャンクごとに SPEC 3.2 の形式のルールを取り出す
3. ルールの文を埋め込み（役割 retrieval）で比べ、既存のルールや同じ資料の中の言い換えを1つにまとめる
   （まとめた側の出典は merged_sources に残す）

抽出したルールは「保留（pending）」で保存し、利用者が画面で確認・承認したものだけを解析に使う。
資料は学外に出さない（data_private に保存し、LLM・埋め込みは おいとまAPI だけを使う）。
"""

import asyncio
import logging
import math
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import BaseModel, ConfigDict, Field

from core.ingest.models import Lang
from core.llm.budget import estimate_tokens, split_text
from core.llm.client import OitomaClient, OitomaError
from core.llm.prompts import load_prompt
from core.rules.models import Checker, CheckType, Rule, RuleLang, RuleSource, TargetSection
from core.rules.store import RuleStore
from core.store.files import data_dir

logger = logging.getLogger(__name__)

ROLE = "rule_extraction"
EMBEDDING_ROLE = "retrieval"
# 埋め込みのコサイン類似度がこれ以上なら、同じルール（言い換え・重複）とみなして統合する
MERGE_THRESHOLD = 0.92
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
_SPACES = re.compile(r"\s+")

DocumentKind = Literal["pdf", "markdown", "text"]
SUFFIXES: dict[str, DocumentKind] = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
}


class ExtractedRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_section: TargetSection
    rule: str = Field(min_length=1, max_length=1000)
    check_type: CheckType
    checker: Checker | None = None
    lang: RuleLang = "both"
    page: int | None = None
    field_tags: list[str] = Field(default_factory=list)


class ExtractedRules(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rules: list[ExtractedRule] = Field(default_factory=list)


# ---------------------------------------------------------------- 資料の保存と読み込み


def document_path(document_id: str, kind: DocumentKind) -> Path:
    if not re.fullmatch(r"RD-[0-9a-f]{12}", document_id):
        raise ValueError("document_id の形式が不正です")
    suffix = {"pdf": ".pdf", "markdown": ".md", "text": ".txt"}[kind]
    return data_dir() / "rules" / f"{document_id}{suffix}"


def save_document_file(document_id: str, kind: DocumentKind, data: bytes) -> Path:
    path = document_path(document_id, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def delete_rule_file(document_id: str, kind: DocumentKind) -> None:
    document_path(document_id, kind).unlink(missing_ok=True)


def read_pages(path: Path, kind: DocumentKind) -> list[tuple[int | None, str]]:
    """(ページ番号, 本文) のリスト。Markdown・テキストはページを持たないので None。"""
    if kind == "pdf":
        with pymupdf.open(path) as pdf:
            return [(i + 1, page.get_text()) for i, page in enumerate(pdf)]
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return [(None, raw.decode(encoding))]
        except UnicodeDecodeError:
            continue
    return [(None, raw.decode("utf-8", errors="replace"))]


def guess_lang(text: str) -> Lang:
    return "ja" if len(_CJK.findall(text[:5000])) >= 20 else "en"


def chunk_pages(pages: list[tuple[int | None, str]], max_tokens: int) -> list[str]:
    """ページの印を付けて連結し、max_tokens 以下のチャンクに分ける（ページの途中でも分ける）。"""
    pieces: list[str] = []
    for page, text in pages:
        body = text.strip()
        if not body:
            continue
        marker = f"[p.{page}]\n" if page is not None else ""
        for part in split_text(body, max_tokens):
            pieces.append(marker + part)
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for piece in pieces:
        cost = estimate_tokens(piece) + 1
        if current and used + cost > max_tokens:
            chunks.append("\n".join(current))
            current, used = [], 0
        current.append(piece)
        used += cost
    if current:
        chunks.append("\n".join(current))
    return chunks


# ---------------------------------------------------------------- 抽出と統合


def normalize_rule(text: str) -> str:
    return _SPACES.sub("", text).rstrip("。．.")


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def to_rule(item: ExtractedRule, *, filename: str, document_id: str) -> Rule:
    # 機械判定は、判定の種類が分かるものだけ。分からなければ LLM で判定する
    check_type = item.check_type
    checker = item.checker if check_type == "mechanical" else None
    if check_type == "mechanical" and checker is None:
        check_type = "llm"
    return Rule(
        rule_id=f"R-{uuid.uuid4().hex[:10]}",
        target_section=item.target_section,
        rule=item.rule.strip(),
        check_type=check_type,
        checker=checker,
        lang=item.lang,
        source=RuleSource(doc=filename, page=item.page),
        field_tags=item.field_tags,
        status="pending",
        document_id=document_id,
    )


def merge_rules(
    new: list[Rule],
    new_vectors: list[list[float] | None],
    existing: dict[str, Rule],
    existing_vectors: dict[str, list[float]],
    *,
    threshold: float = MERGE_THRESHOLD,
) -> tuple[list[tuple[Rule, list[float] | None]], dict[str, Rule]]:
    """新しいルールを、既存のルール・先に残した新しいルールと比べて統合する。

    戻り値：(追加するルールと埋め込み, 出典を足して更新する既存のルール)
    同じ文面（空白・句点を除いて一致）か、埋め込みが threshold 以上なら同じルールとみなす。
    """
    kept: list[tuple[Rule, list[float] | None]] = []
    updated: dict[str, Rule] = {}

    def same(a: Rule, va: list[float] | None, b: Rule, vb: list[float] | None) -> bool:
        # 同じ種類の機械判定は同じルール（組み込みのルールに出典をまとめる）
        if a.check_type == b.check_type == "mechanical":
            return a.checker == b.checker
        if a.target_section != b.target_section and "全体" not in (
            a.target_section,
            b.target_section,
        ):
            return False
        if normalize_rule(a.rule) == normalize_rule(b.rule):
            return True
        return va is not None and vb is not None and cosine(va, vb) >= threshold

    for rule, vector in zip(new, new_vectors, strict=True):
        target: Rule | None = None
        for rid, other in existing.items():
            if same(rule, vector, other, existing_vectors.get(rid)):
                target = updated.get(rid, other)
                break
        if target is not None:
            updated[target.rule_id] = target.model_copy(
                update={"merged_sources": [*target.merged_sources, rule.source]}
            )
            continue
        for i, (other, other_vector) in enumerate(kept):
            if same(rule, vector, other, other_vector):
                kept[i] = (
                    other.model_copy(
                        update={"merged_sources": [*other.merged_sources, rule.source]}
                    ),
                    other_vector,
                )
                break
        else:
            kept.append((rule, vector))
    return kept, updated


async def extract_rules(
    client: OitomaClient,
    store: RuleStore,
    document_id: str,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """資料からルールを抽出して保存し、新しく追加したルールの数を返す。"""
    doc = store.get_document(document_id)
    if doc is None:
        raise ValueError(f"ルール資料 {document_id} が見つかりません")
    pages = read_pages(document_path(document_id, doc.kind), doc.kind)
    lang = guess_lang("\n".join(text for _, text in pages))
    system = load_prompt(ROLE, lang)
    cfg = client.config.role(ROLE)
    max_output = cfg.max_tokens or client.config.api.default_max_tokens
    # 出力（ルールの JSON）が入力より長くならないよう、入力を出力上限より小さく抑える
    chunks = chunk_pages(pages, max_tokens=max(500, max_output // 2))
    done = 0

    async def extract(chunk: str) -> list[ExtractedRule]:
        nonlocal done
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": load_prompt(
                    "rule_extraction_input", lang, filename=doc.filename, text=chunk
                ),
            },
        ]
        try:
            result = await client.chat_json(ROLE, messages, ExtractedRules, lang=lang)
            return result.rules
        finally:
            done += 1
            if on_progress is not None:
                on_progress(done, len(chunks))

    batches = await asyncio.gather(*(extract(c) for c in chunks))
    new = [
        to_rule(item, filename=doc.filename, document_id=document_id) for b in batches for item in b
    ]
    if not new:
        return 0
    try:
        vectors: list[list[float] | None] = list(
            await client.embed([r.rule for r in new], role=EMBEDDING_ROLE)
        )
    except OitomaError as exc:
        # 埋め込みが使えなくても、同じ文面の統合だけ行って保存する
        logger.warning(
            "ルールの埋め込みに失敗しました（%s）。文面の一致だけで統合します", type(exc).__name__
        )
        vectors = [None] * len(new)
    existing = {r.rule_id: r for r in store.list_rules()}
    kept, updated = merge_rules(new, vectors, existing, store.embeddings())
    for rule in updated.values():
        store.replace_rule(rule)
    store.add_rules([r for r, _ in kept], [v for _, v in kept])
    return len(kept)
