"""専門知識の資料から、過去の指摘と研究室のルールを取り出す（SPEC 3.1・3.2）。

1. 資料（PDF / DOCX / Markdown / テキスト）をチャンクに分け、高速モデル（役割 lab_extraction）で
   過去の指摘（一般化したもの）と研究室のルール（SPEC 3.2 の形式）を取り出す
2. 過去の指摘は埋め込み（役割 retrieval）とともに研究室ID を付けて保存する（類似箇所の検出に使う）
3. 研究室のルールは、出典を「研究室：過去査読（資料名）」としてルールの一覧に「未確認」で加える
   （M6 のルールと同じく、重複・言い換えは統合し、利用者が承認したものだけを解析に使う）

資料は学外に出さない（data_private に保存し、LLM・埋め込みは おいとまAPI だけを使う）。
"""

import asyncio
import logging
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from core.ingest import textdoc
from core.ingest.textdoc import DocumentKind, chunk_pages, guess_lang, read_pages
from core.lab.models import LabCategory, LabComment, LabSource
from core.lab.store import LabStore
from core.llm.client import OitomaClient, OitomaError
from core.llm.prompts import load_prompt
from core.rules.extract import ExtractedRule, merge_rules, to_rule
from core.rules.store import RuleStore

logger = logging.getLogger(__name__)

ROLE = "lab_extraction"
EMBEDDING_ROLE = "retrieval"
# ルールの出典の表記（SPEC 3.2）
LAB_SOURCE_PREFIX = "研究室：過去査読"


class ExtractedComment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    comment: str = Field(min_length=1, max_length=500)
    example: str | None = Field(default=None, max_length=500)
    category: LabCategory = "other"
    page: int | None = None


class ExtractedLabKnowledge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    comments: list[ExtractedComment] = Field(default_factory=list)
    rules: list[ExtractedRule] = Field(default_factory=list)


def document_path(document_id: str, kind: DocumentKind) -> Path:
    return textdoc.document_path("lab", document_id, kind)


def save_document_file(document_id: str, kind: DocumentKind, data: bytes) -> Path:
    return textdoc.save_file(document_path(document_id, kind), data)


def delete_lab_file(document_id: str, kind: DocumentKind) -> None:
    document_path(document_id, kind).unlink(missing_ok=True)


async def extract_lab_knowledge(
    client: OitomaClient, lab_store: LabStore, rule_store: RuleStore, document_id: str
) -> tuple[int, int]:
    """資料から過去の指摘と研究室のルールを取り出して保存し、(指摘の数, 追加したルールの数) を返す。

    取り出した指摘・ルールは資料の所有者のものにし、統合の判定にも所有者のルールだけを使う。
    """
    found = lab_store.find_document(document_id)
    if found is None:
        raise ValueError(f"専門知識の資料 {document_id} が見つかりません")
    doc, owner = found
    pages = read_pages(document_path(document_id, doc.kind), doc.kind)
    lang = guess_lang("\n".join(text for _, text in pages))
    system = load_prompt(ROLE, lang, output_language="日本語" if lang == "ja" else "English")
    cfg = client.config.role(ROLE)
    max_output = cfg.max_tokens or client.config.api.default_max_tokens
    chunks = chunk_pages(pages, max_tokens=max(500, max_output // 2))

    async def extract(chunk: str) -> ExtractedLabKnowledge:
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": load_prompt(
                    "lab_extraction_input", lang, filename=doc.filename, text=chunk
                ),
            },
        ]
        return await client.chat_json(ROLE, messages, ExtractedLabKnowledge, lang=lang)

    results = await asyncio.gather(*(extract(c) for c in chunks))
    comments = [
        LabComment(
            comment_id=f"LC-{uuid.uuid4().hex[:10]}",
            document_id=document_id,
            comment=item.comment.strip(),
            example=(item.example or "").strip() or None,
            category=item.category,
            source=LabSource(doc=doc.filename, page=item.page),
        )
        for result in results
        for item in result.comments
    ]
    source_name = f"{LAB_SOURCE_PREFIX}（{doc.filename}）"
    rules = [
        to_rule(item, filename=source_name, document_id=document_id)
        for result in results
        for item in result.rules
        if item.check_type != "mechanical"  # 機械判定は組み込みのルールで足りる
    ]
    texts = [c.comment for c in comments] + [r.rule for r in rules]
    vectors: list[list[float] | None]
    try:
        vectors = list(await client.embed(texts, role=EMBEDDING_ROLE)) if texts else []
    except OitomaError as exc:
        logger.warning("専門知識の埋め込みに失敗しました（%s）", type(exc).__name__)
        vectors = [None] * len(texts)
    lab_store.add_comments(owner, comments, vectors[: len(comments)])
    added = 0
    if rules:
        existing = {r.rule_id: r for r in rule_store.list_rules(owner)}
        kept, updated = merge_rules(
            rules, vectors[len(comments) :], existing, rule_store.embeddings(owner)
        )
        for rule in updated.values():
            rule_store.replace_rule(owner, rule)
        rule_store.add_rules(owner, [r for r, _ in kept], [v for _, v in kept])
        added = len(kept)
    return len(comments), added
