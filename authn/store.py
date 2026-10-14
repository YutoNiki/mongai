"""ログインのセッションと、ログインの途中の状態（OIDC の state・nonce・PKCE）を保存する SQLite。

認証サービス専用のデータベース（authn_data ボリューム）で、論文のデータベースとは別にする。
- セッション ID・ブラウザの照合用の値は SHA-256 のハッシュだけを保存する（データベースが漏れても Cookie を作れない）
- 保存するのは、外部の識別子（iss・sub）と、表示・許可の判定に使うメールアドレスと、日時だけ。
  Google のアクセストークン・ID トークン・リフレッシュトークンは保存しない
- 期限の切れたセッション・ログインの途中の状態は、ログインと確認のたびに削除する
"""

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_hash TEXT PRIMARY KEY,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_expiry ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS login_flows (
    state TEXT PRIMARY KEY,
    browser_hash TEXT NOT NULL,
    nonce TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    next_path TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""

# ログインの途中の状態の有効期間（Google の画面での操作を待つ時間）
FLOW_LIFETIME_S = 10 * 60
# 同時に保存しておくログインの途中の状態の上限（ログインの開始を繰り返してもデータベースを膨らませない）
MAX_FLOWS = 10_000
# 最後の利用日時を書き込む間隔（要求のたびに書き込まない）
LAST_SEEN_INTERVAL_S = 5 * 60


class StoreError(Exception):
    """セッションのストアを読み書きできない（認証済みとはみなさない）。"""


def new_secret() -> str:
    """推測できない値（256 ビット）。Cookie のセッション ID などに使う。"""
    return secrets.token_urlsafe(32)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Session:
    issuer: str
    subject: str
    email: str
    created_at: int
    expires_at: int


@dataclass(frozen=True)
class LoginFlow:
    nonce: str
    code_verifier: str
    next_path: str
    created_at: int


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(SCHEMA)
            path.chmod(0o600)
        except (OSError, sqlite3.Error) as exc:
            raise StoreError("セッションのストアを準備できない") from exc

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            conn = sqlite3.connect(self.path, timeout=5)
        except sqlite3.Error as exc:
            raise StoreError("セッションのストアを開けない") from exc
        try:
            with conn:
                yield conn
        except sqlite3.Error as exc:
            raise StoreError("セッションのストアを読み書きできない") from exc
        finally:
            conn.close()

    # ------------------------------------------------------------ ログインの途中の状態

    def create_flow(
        self, state: str, browser_secret: str, nonce: str, code_verifier: str, next_path: str
    ) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute("DELETE FROM login_flows WHERE created_at < ?", (now - FLOW_LIFETIME_S,))
            (count,) = conn.execute("SELECT COUNT(*) FROM login_flows").fetchone()
            if count >= MAX_FLOWS:
                raise StoreError("ログインの途中の状態が多すぎる")
            conn.execute(
                "INSERT INTO login_flows (state, browser_hash, nonce, code_verifier, next_path, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (state, digest(browser_secret), nonce, code_verifier, next_path, now),
            )

    def take_flow(self, state: str, browser_secret: str) -> LoginFlow | None:
        """state に対応する状態を取り出して消す（1回だけ使える）。ブラウザ・期限が合わなければ None。"""
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                "DELETE FROM login_flows WHERE state = ? "
                "RETURNING browser_hash, nonce, code_verifier, next_path, created_at",
                (state,),
            ).fetchone()
        if row is None:
            return None
        browser_hash, nonce, code_verifier, next_path, created_at = row
        if not hmac.compare_digest(browser_hash, digest(browser_secret)):
            return None
        if created_at < now - FLOW_LIFETIME_S:
            return None
        return LoginFlow(nonce, code_verifier, next_path, created_at)

    # ------------------------------------------------------------ セッション

    def create_session(
        self, issuer: str, subject: str, email: str, *, lifetime_s: int
    ) -> tuple[str, Session]:
        """新しいセッションを作り、Cookie に入れる ID（ハッシュにする前の値）を返す。"""
        session_id = new_secret()
        now = int(time.time())
        session = Session(issuer, subject, email, now, now + lifetime_s)
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO sessions (session_hash, issuer, subject, email, created_at, "
                "expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (digest(session_id), issuer, subject, email, now, session.expires_at, now),
            )
        return session_id, session

    def get_session(self, session_id: str, *, idle_s: int) -> Session | None:
        """有効なセッション。期限切れ・一定時間使われなかったものは消して None。"""
        now = int(time.time())
        key = digest(session_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT issuer, subject, email, created_at, expires_at, last_seen_at "
                "FROM sessions WHERE session_hash = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            issuer, subject, email, created_at, expires_at, last_seen_at = row
            if expires_at <= now or last_seen_at + idle_s <= now:
                conn.execute("DELETE FROM sessions WHERE session_hash = ?", (key,))
                return None
            if now - last_seen_at >= LAST_SEEN_INTERVAL_S:
                conn.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE session_hash = ?", (now, key)
                )
        return Session(issuer, subject, email, created_at, expires_at)

    def delete_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE session_hash = ?", (digest(session_id),))

    def delete_all_sessions(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions")
        return cur.rowcount
