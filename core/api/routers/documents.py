"""論文（PDF）のアップロード・取得・削除。"""

import os
import secrets

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response

from core.api.dependencies import StoreDep
from core.api.schemas import UploadResponse
from core.sample import is_sample
from core.store import PaperBusyError
from core.store.files import delete_paper_file, doc_id_for, load_paper, save_paper

MAX_UPLOAD_BYTES = 100 * 1024 * 1024

router = APIRouter()


@router.post("/documents", status_code=201)
async def upload_document(file: UploadFile, store: StoreDep) -> UploadResponse:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="ファイルが大きすぎます（上限 100MB）")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="PDF ファイルを選んでください")
    doc_id = doc_id_for(data)
    if is_sample(doc_id):
        # デモと同じ PDF でも、デモとは別の論文にする（デモの論文に解析が付かないように）
        doc_id = secrets.token_hex(8)
    save_paper(data, doc_id)
    filename = os.path.basename(file.filename or "paper.pdf")[:255]
    store.save_paper(doc_id, filename=filename, size=len(data))
    return UploadResponse(doc_id=doc_id, filename=filename, size=len(data))


@router.get("/documents/{doc_id}/paper.pdf")
def paper_pdf(doc_id: str) -> Response:
    try:
        data = load_paper(doc_id)
    except ValueError:
        data = None
    if data is None:
        raise HTTPException(status_code=404, detail="論文のファイルが見つかりません")
    return Response(content=data, media_type="application/pdf")


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str, store: StoreDep) -> Response:
    """論文の PDF と、その論文のすべての解析結果を削除する（外部通信ログは追記専用なので残る）。"""
    if store.get_paper(doc_id) is None:
        raise HTTPException(status_code=404, detail="論文が見つかりません")
    if is_sample(doc_id):
        raise HTTPException(status_code=403, detail="デモの論文は削除できません")
    try:
        store.delete_paper(doc_id)
    except PaperBusyError:
        raise HTTPException(
            status_code=409,
            detail="解析中・順番待ちの論文は削除できません。解析が終わってから削除してください",
        ) from None
    delete_paper_file(doc_id)
    return Response(status_code=204)
