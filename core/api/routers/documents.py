"""論文（PDF）のアップロード・取得・削除。"""

import os
import sqlite3

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response

from core.api.dependencies import PrincipalDep, StoreDep, UserDep
from core.api.queries import owned_paper, readable_paper
from core.api.schemas import UploadResponse
from core.store import PaperBusyError, PaperNotFoundError
from core.store.files import content_sha256, delete_paper_file, load_paper, new_doc_id, save_paper

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
# ID（64ビットの乱数）が既存の論文と重なったときに作り直す回数
MAX_ID_ATTEMPTS = 5

router = APIRouter()


@router.post("/documents", status_code=201)
async def upload_document(file: UploadFile, store: StoreDep, user: UserDep) -> UploadResponse:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="ファイルが大きすぎます（上限 100MB）")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="PDF ファイルを選んでください")
    filename = os.path.basename(file.filename or "paper.pdf")[:255]
    digest = content_sha256(data)
    # 同じ PDF でも、利用者・アップロードごとに別の論文にする（ID は内容から作らない）
    for _ in range(MAX_ID_ATTEMPTS):
        doc_id = new_doc_id()
        try:
            store.save_paper(
                doc_id,
                owner_user_id=user.user_id,
                filename=filename,
                size=len(data),
                content_sha256=digest,
            )
        except sqlite3.IntegrityError:
            continue
        try:
            save_paper(data, doc_id)
        except OSError:
            store.delete_paper(doc_id, owner_user_id=user.user_id)
            raise
        return UploadResponse(doc_id=doc_id, filename=filename, size=len(data))
    raise HTTPException(status_code=500, detail="論文の ID を割り当てられませんでした")


@router.get("/documents/{doc_id}/paper.pdf")
def paper_pdf(doc_id: str, store: StoreDep, principal: PrincipalDep) -> Response:
    readable_paper(store, principal, doc_id)  # ファイルを読む前に、主体が読める論文か確かめる
    try:
        data = load_paper(doc_id)
    except ValueError:
        data = None
    if data is None:
        raise HTTPException(status_code=404, detail="論文のファイルが見つかりません")
    return Response(content=data, media_type="application/pdf")


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str, store: StoreDep, user: UserDep) -> Response:
    """論文の PDF と、その論文のすべての解析結果を削除する（外部通信ログは追記専用なので残る）。"""
    owned_paper(store, user, doc_id)
    try:
        store.delete_paper(doc_id, owner_user_id=user.user_id)
    except PaperNotFoundError:
        raise HTTPException(status_code=404, detail="論文が見つかりません") from None
    except PaperBusyError:
        raise HTTPException(
            status_code=409,
            detail="解析中・順番待ちの論文は削除できません。解析が終わってから削除してください",
        ) from None
    # 所有権を確かめて DB から消した後にファイルを消す（doc_id は利用者ごとに異なるので他人に影響しない）
    delete_paper_file(doc_id)
    return Response(status_code=204)
