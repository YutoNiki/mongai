"""専門知識（研究室の過去の指摘。SPEC 3.1〜3.3）。

すべて、この MongAI が扱う研究室（current_lab_id）のデータだけを読み書きする。
"""

import contextlib
import os

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response

from core.api.dependencies import LabStoreDep, QueueDep, RuleStoreDep
from core.api.routers.rules import MAX_RULE_DOCUMENT_BYTES
from core.ingest.textdoc import SUFFIXES, looks_valid
from core.lab.extract import delete_lab_file
from core.lab.extract import save_document_file as save_lab_file
from core.lab.models import LabComment, LabDocument, current_lab_id

router = APIRouter()


@router.get("/lab/documents")
def list_lab_documents(lab: LabStoreDep) -> list[LabDocument]:
    return lab.list_documents(current_lab_id())


@router.post("/lab/documents", status_code=201)
async def upload_lab_document(file: UploadFile, lab: LabStoreDep, queue: QueueDep) -> LabDocument:
    """過去の査読コメント・回答書・添削メモ（PDF・DOCX・Markdown・テキスト）を登録し、取り込みを始める。"""
    filename = os.path.basename(file.filename or "notes.txt")[:255]
    kind = SUFFIXES.get(os.path.splitext(filename)[1].lower())
    if kind is None:
        raise HTTPException(
            status_code=415,
            detail="PDF・Word（.docx）・Markdown（.md）・テキスト（.txt）のファイルを選んでください",
        )
    data = await file.read(MAX_RULE_DOCUMENT_BYTES + 1)
    if len(data) > MAX_RULE_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="ファイルが大きすぎます（上限 30MB）")
    if not looks_valid(kind, data):
        raise HTTPException(status_code=415, detail="ファイルの形式が拡張子と合いません")
    doc = lab.create_document(current_lab_id(), filename, kind)
    save_lab_file(doc.document_id, kind, data)
    await queue.enqueue_lab(doc.document_id)
    return doc


@router.delete("/lab/documents/{document_id}", status_code=204)
def delete_lab_document(document_id: str, lab: LabStoreDep, rules: RuleStoreDep) -> Response:
    """資料と、そこから取り出した過去の指摘・研究室のルールを削除する。"""
    doc = lab.get_document(current_lab_id(), document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="専門知識の資料が見つかりません")
    if doc.status in ("queued", "running"):
        raise HTTPException(status_code=409, detail="取り込み中は削除できません")
    lab.delete_document(current_lab_id(), document_id)
    rules.delete_document(document_id)  # この資料から取り出したルール
    with contextlib.suppress(ValueError):
        delete_lab_file(document_id, doc.kind)
    return Response(status_code=204)


@router.get("/lab/comments")
def list_lab_comments(lab: LabStoreDep) -> list[LabComment]:
    return lab.list_comments(current_lab_id())


@router.delete("/lab/comments/{comment_id}", status_code=204)
def delete_lab_comment(comment_id: str, lab: LabStoreDep) -> Response:
    if not lab.delete_comment(current_lab_id(), comment_id):
        raise HTTPException(status_code=404, detail="過去の指摘が見つかりません")
    return Response(status_code=204)
