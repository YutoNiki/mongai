"""core に渡す主張（短命の JWT、EdDSA・Ed25519）に署名する鍵。

- 秘密鍵は認証サービス専用のボリューム（authn_data）に、所有者だけが読めるファイル（0600）で保存する。
  再起動しても同じ鍵を使う。初めての起動で鍵が無ければ作る
- 公開鍵は別のボリューム（authn_public）に書き、core が読み取り専用でマウントする（core に秘密鍵は渡さない）
- 鍵の ID は authn- で始める。開発用のモック認証サービスの鍵（dev-）は使わない
- 主張の形式は core/auth/assertion.py の検証に合わせる
"""

import base64
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

ISSUER = "mongai-authn"
AUDIENCE = "mongai-core"
KID_PREFIX = "authn-"
ASSERTION_LIFETIME_S = 60
PRIVATE_KEY_NAME = "signing_key.json"


class SigningKeyError(Exception):
    """署名の鍵を読めない・不正（起動を止める）。"""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _write_atomic(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        tmp.chmod(mode)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class SigningKey:
    kid: str
    key: Ed25519PrivateKey

    @classmethod
    def generate(cls) -> "SigningKey":
        return cls(f"{KID_PREFIX}{secrets.token_hex(8)}", Ed25519PrivateKey.generate())

    def public_json(self) -> str:
        public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return json.dumps({"kid": self.kid, "public_key": base64.b64encode(public).decode()})

    def private_json(self) -> str:
        raw = self.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        return json.dumps({"kid": self.kid, "private_key": base64.b64encode(raw).decode()})

    def sign_assertion(self, *, idp: str, subject: str, email: str) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + ASSERTION_LIFETIME_S,
            "idp": idp,
            "sub": subject,
            "email": email,
            "email_verified": True,
            "name": None,
        }
        header = _b64url(json.dumps({"alg": "EdDSA", "typ": "JWT", "kid": self.kid}).encode())
        payload = _b64url(json.dumps(claims).encode())
        signature = _b64url(self.key.sign(f"{header}.{payload}".encode()))
        return f"{header}.{payload}.{signature}"


def load_private_key(path: Path) -> SigningKey:
    try:
        data = json.loads(path.read_text())
        kid = str(data["kid"])
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(data["private_key"]))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SigningKeyError(f"署名の鍵（{path}）を読めない") from exc
    if not kid.startswith(KID_PREFIX):
        raise SigningKeyError("署名の鍵の ID が authn- で始まらない（開発用の鍵は使えない）")
    return SigningKey(kid, key)


def write_keys(key: SigningKey, data_dir: Path, public_key_file: Path) -> None:
    _write_atomic(data_dir / PRIVATE_KEY_NAME, key.private_json(), 0o600)
    _write_atomic(public_key_file, key.public_json(), 0o644)


def ensure_signing_key(data_dir: Path, public_key_file: Path) -> SigningKey:
    """保存した鍵を読む（無ければ作る）。公開鍵のファイルは、いつも秘密鍵から書き直す。"""
    path = data_dir / PRIVATE_KEY_NAME
    if path.exists():
        key = load_private_key(path)
        if path.stat().st_mode & 0o077:
            path.chmod(0o600)
    else:
        key = SigningKey.generate()
        _write_atomic(path, key.private_json(), 0o600)
    _write_atomic(public_key_file, key.public_json(), 0o644)
    return key


def rotate_signing_key(data_dir: Path, public_key_file: Path) -> SigningKey:
    """新しい鍵に替える。認証サービスの再起動で使われ始める（core は公開鍵の更新を自動で読み直す）。"""
    key = SigningKey.generate()
    write_keys(key, data_dir, public_key_file)
    return key
