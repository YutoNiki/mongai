"""テスト用の利用者・主体・署名つきの主張（合成データ）。"""

import base64
import json
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.store.users import UserStore


def make_user(db: Path, name: str = "user") -> str:
    """データベースに利用者を作り、内部ユーザーIDを返す。"""
    return UserStore(db).create_user(display_name=name, email=f"{name}@example.ac.jp")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Signer:
    """認証サービスの代わりに主張に署名する（公開鍵のファイルを key_file に書く）。"""

    def __init__(self, key_file: Path, kid: str = "test-key") -> None:
        self.kid = kid
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        key_file.write_text(
            json.dumps({"kid": kid, "public_key": base64.b64encode(public).decode()})
        )
        self.key_file = key_file

    def token(self, sub: str = "alice", **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": "mongai-authn",
            "aud": "mongai-core",
            "iat": now,
            "exp": now + 300,
            "idp": "https://idp.example",
            "sub": sub,
            "email": f"{sub}@example.ac.jp",
            "email_verified": True,
            "name": sub,
            **overrides,
        }
        header = _b64(json.dumps({"alg": "EdDSA", "typ": "JWT", "kid": self.kid}).encode())
        payload = _b64(json.dumps(claims).encode())
        signature = _b64(self.key.sign(f"{header}.{payload}".encode()))
        return f"{header}.{payload}.{signature}"
