"""利用者（内部ユーザーID）と、認証プロバイダーの識別子（issuer・subject）との対応付け。

データの所有者は内部ユーザーID（U-…）で記録する。メールアドレスは表示用で、所有者の判定に使わない。
"""

import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from core.store.db import DEFAULT_DB_PATH
from core.store.schema import connect, ensure_schema

UserStatus = Literal["active", "disabled"]


class UserRecord(BaseModel):
    user_id: str
    display_name: str | None
    email: str | None
    status: UserStatus
    created_at: str
    last_login_at: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_user_id() -> str:
    return f"U-{uuid.uuid4().hex}"


class UserStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("MONGAI_DB_PATH") or DEFAULT_DB_PATH)
        ensure_schema(self.path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create_user(self, *, display_name: str | None = None, email: str | None = None) -> str:
        user_id = new_user_id()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users (user_id, display_name, email, created_at) VALUES (?, ?, ?, ?)",
                (user_id, display_name, email, _now()),
            )
        return user_id

    def create_with_identity(
        self, issuer: str, subject: str, *, display_name: str | None, email: str | None
    ) -> str:
        """外部の識別子に対応する利用者を作る（同時に作られていれば、その利用者の ID を返す）。"""
        user_id = new_user_id()
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT user_id FROM user_identities WHERE issuer = ? AND subject = ?",
                (issuer, subject),
            ).fetchone()
            if row is not None:
                return str(row[0])
            conn.execute(
                "INSERT INTO users (user_id, display_name, email, created_at, last_login_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, display_name, email, now, now),
            )
            conn.execute(
                "INSERT INTO user_identities (issuer, subject, user_id, created_at, last_login_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (issuer, subject, user_id, now, now),
            )
        return user_id

    def get_user(self, user_id: str) -> UserRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return UserRecord(**dict(row)) if row else None

    def find_by_identity(self, issuer: str, subject: str) -> UserRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT u.* FROM users u JOIN user_identities i ON i.user_id = u.user_id "
                "WHERE i.issuer = ? AND i.subject = ?",
                (issuer, subject),
            ).fetchone()
        return UserRecord(**dict(row)) if row else None

    def link_identity(self, user_id: str, issuer: str, subject: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_identities (issuer, subject, user_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (issuer, subject, user_id, _now()),
            )

    def record_login(
        self,
        user_id: str,
        issuer: str,
        subject: str,
        *,
        display_name: str | None,
        email: str | None,
    ) -> None:
        """最後のログイン日時と、表示用の名前・メールアドレスを更新する。"""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ?, display_name = COALESCE(?, display_name), "
                "email = COALESCE(?, email) WHERE user_id = ?",
                (now, display_name, email, user_id),
            )
            conn.execute(
                "UPDATE user_identities SET last_login_at = ? WHERE issuer = ? AND subject = ?",
                (now, issuer, subject),
            )

    def set_status(self, user_id: str, status: UserStatus) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET status = ? WHERE user_id = ?", (status, user_id))
