"""アップロードされた論文の保存先（data_private ボリューム、/data/private）。

論文は学外に出さないため、このディレクトリは core / worker にだけマウントする。
"""

import hashlib
import os
import re
from pathlib import Path

DEFAULT_DATA_DIR = "/data/private"
_DOC_ID = re.compile(r"[0-9a-f]{16}")


def data_dir() -> Path:
    return Path(os.environ.get("MONGAI_DATA_DIR") or DEFAULT_DATA_DIR)


def doc_id_for(data: bytes) -> str:
    """論文の ID（内容の SHA-256 の先頭16桁。core.ingest.pipeline と同じ）。"""
    return hashlib.sha256(data).hexdigest()[:16]


def paper_path(doc_id: str) -> Path:
    if not _DOC_ID.fullmatch(doc_id):  # パスの組み立てに使うため、形式を厳密に確認する
        raise ValueError("doc_id の形式が不正です")
    return data_dir() / "papers" / f"{doc_id}.pdf"


def save_paper(data: bytes, doc_id: str | None = None) -> str:
    """論文を保存して ID を返す。doc_id を省くと内容のハッシュから決める。"""
    doc_id = doc_id or doc_id_for(data)
    path = paper_path(doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    return doc_id


def load_paper(doc_id: str) -> bytes | None:
    path = paper_path(doc_id)
    return path.read_bytes() if path.exists() else None


def delete_paper_file(doc_id: str) -> None:
    paper_path(doc_id).unlink(missing_ok=True)
