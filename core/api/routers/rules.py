"""ルールとルール資料（SPEC 3.2）。すべて主体（利用者）のルール・資料だけを読み書きする。"""

import contextlib
import os

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response

from core.api.dependencies import QueueDep, RuleStoreDep, UserDep
from core.api.schemas import BulkRuleStatus
from core.ingest.textdoc import SUFFIXES, looks_valid
from core.rules.extract import delete_rule_file, save_document_file
from core.rules.models import Rule, RuleDocument, RuleStatus, RuleUpdate
from core.rules.store import RuleNotFoundError

MAX_RULE_DOCUMENT_BYTES = 30 * 1024 * 1024

router = APIRouter()


@router.get("/rules")
def list_rules(rules: RuleStoreDep, user: UserDep, status: RuleStatus | None = None) -> list[Rule]:
    """自分のルールと、組み込みのルール（自分の設定を重ねたもの）。"""
    return rules.list_rules(user.user_id, status=status)


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: str, update: RuleUpdate, rules: RuleStoreDep, user: UserDep) -> Rule:
    """ルールの確認・編集・承認・無効化。組み込みのルールは自分の有効・無効だけを変えられる。"""
    try:
        return rules.update_rule(user.user_id, rule_id, update)
    except RuleNotFoundError:
        raise HTTPException(status_code=404, detail="ルールが見つかりません") from None


@router.post("/rules/bulk-status")
def bulk_rule_status(request: BulkRuleStatus, rules: RuleStoreDep, user: UserDep) -> list[Rule]:
    """複数のルールをまとめて承認・無効・保留にする（見つからない ID・他人のルールは無視する）。"""
    updated: list[Rule] = []
    for rule_id in request.rule_ids:
        try:
            updated.append(
                rules.update_rule(user.user_id, rule_id, RuleUpdate(status=request.status))
            )
        except RuleNotFoundError:
            continue
    return updated


@router.get("/rule-documents")
def list_rule_documents(rules: RuleStoreDep, user: UserDep) -> list[RuleDocument]:
    return rules.list_documents(user.user_id)


@router.post("/rule-documents", status_code=201)
async def upload_rule_document(
    file: UploadFile, rules: RuleStoreDep, queue: QueueDep, user: UserDep
) -> RuleDocument:
    """ルール資料（PDF・DOCX・Markdown・テキスト）を登録し、ルールの抽出を始める。資料は data_private に保存する。"""
    filename = os.path.basename(file.filename or "rules.txt")[:255]
    kind = SUFFIXES.get(os.path.splitext(filename)[1].lower())
    if kind is None:
        raise HTTPException(
            status_code=415,
            detail="PDF・Markdown（.md）・テキスト（.txt）のファイルを選んでください",
        )
    data = await file.read(MAX_RULE_DOCUMENT_BYTES + 1)
    if len(data) > MAX_RULE_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="ファイルが大きすぎます（上限 30MB）")
    if not looks_valid(kind, data):
        raise HTTPException(status_code=415, detail="ファイルの形式が拡張子と合いません")
    doc = rules.create_document(user.user_id, filename, kind)
    save_document_file(doc.document_id, kind, data)
    await queue.enqueue_rules(doc.document_id)
    return doc


@router.delete("/rule-documents/{document_id}", status_code=204)
def delete_rule_document(document_id: str, rules: RuleStoreDep, user: UserDep) -> Response:
    """資料と、そこから抽出したルールを削除する（ほかの資料のルールに統合された出典の記録は残る）。"""
    doc = rules.get_document(user.user_id, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="ルール資料が見つかりません")
    if doc.status in ("queued", "running"):
        raise HTTPException(status_code=409, detail="ルールの抽出中は削除できません")
    rules.delete_document(user.user_id, document_id)
    with contextlib.suppress(ValueError):
        delete_rule_file(document_id, doc.kind)
    return Response(status_code=204)
