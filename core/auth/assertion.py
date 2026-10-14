"""認証サービス（authn）が付けた、署名つきの主張の検証。

web（nginx）が認証サービスに問い合わせ、その応答の主張をヘッダー `X-MongAI-Assertion` で core に渡す。
主張は短命の JWT（EdDSA・Ed25519）で、core は認証サービスの公開鍵だけを持つ（core は主張を作れない）。
core は外部の認証プロバイダーと直接通信しない。

公開鍵のファイル（環境変数 MONGAI_AUTH_PUBLIC_KEY_FILE）は JSON：{"kid": "...", "public_key": "<base64>"}。
設定が無ければ、提示された主張はすべて無効（401）にする。ヘッダーの利用者IDをそのまま信用する経路は作らない。
"""

import base64
import binascii
import json
import os
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ValidationError

ASSERTION_HEADER = "X-MongAI-Assertion"
ISSUER = "mongai-authn"
AUDIENCE = "mongai-core"
DEV_KID_PREFIX = "dev-"
CLOCK_SKEW_S = 30
MAX_LIFETIME_S = 15 * 60


class InvalidAssertion(Exception):
    """主張が無効（署名不正・期限切れ・形式違い・未設定・無効化された利用者など）。"""


class ExternalIdentity(BaseModel):
    issuer: str  # 認証プロバイダーの iss
    subject: str  # 認証プロバイダーの sub（プロバイダー内で不変の ID）
    email: str | None = None  # 表示用（所有者の判定には使わない）
    email_verified: bool = False
    name: str | None = None


class _Claims(BaseModel):
    iss: str
    aud: str
    exp: int
    iat: int
    idp: str
    sub: str
    email: str | None = None
    email_verified: bool = False
    name: str | None = None


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def is_production() -> bool:
    return os.environ.get("MONGAI_ENV", "").lower() == "production"


class PublicKey(BaseModel):
    kid: str
    public_key: str  # Ed25519 の公開鍵（32バイト）の base64


class AssertionVerifier:
    """公開鍵のファイルを読み、主張を検証する（ファイルが更新されたら読み直す）。"""

    def __init__(self, key_file: str | Path | None = None) -> None:
        configured = key_file or os.environ.get("MONGAI_AUTH_PUBLIC_KEY_FILE")
        self.key_file = Path(configured) if configured else None
        self._loaded: tuple[float, str, Ed25519PublicKey] | None = None

    def _key(self) -> tuple[str, Ed25519PublicKey]:
        if self.key_file is None:
            raise InvalidAssertion("認証サービスの公開鍵が設定されていない")
        try:
            mtime = self.key_file.stat().st_mtime
            if self._loaded is None or self._loaded[0] != mtime:
                data = PublicKey.model_validate_json(self.key_file.read_text())
                key = Ed25519PublicKey.from_public_bytes(base64.b64decode(data.public_key))
                self._loaded = (mtime, data.kid, key)
        except (OSError, ValueError, ValidationError, binascii.Error) as exc:
            raise InvalidAssertion("認証サービスの公開鍵を読めない") from exc
        kid, key = self._loaded[1], self._loaded[2]
        if is_production() and kid.startswith(DEV_KID_PREFIX):
            raise InvalidAssertion("本番の構成で開発用の鍵は使えない")
        return kid, key

    def check_configuration(self) -> None:
        """起動時の確認：本番の構成で開発用の鍵が設定されていたら起動しない。"""
        if self.key_file is None or not self.key_file.exists():
            return
        try:
            kid = PublicKey.model_validate_json(self.key_file.read_text()).kid
        except (OSError, ValidationError):
            return  # 読めない鍵では、どの主張も通らない（要求のたびに 401）
        if is_production() and kid.startswith(DEV_KID_PREFIX):
            raise RuntimeError("本番の構成（MONGAI_ENV=production）で開発用の鍵が設定されている")

    def verify(self, token: str) -> ExternalIdentity:
        kid, key = self._key()
        parts = token.split(".")
        if len(parts) != 3:
            raise InvalidAssertion("主張の形式が不正")
        try:
            header: dict[str, Any] = json.loads(_b64decode(parts[0]))
            signature = _b64decode(parts[2])
        except (ValueError, binascii.Error) as exc:
            raise InvalidAssertion("主張の形式が不正") from exc
        if header.get("alg") != "EdDSA" or header.get("kid") != kid:
            raise InvalidAssertion("主張の鍵・方式が違う")
        try:
            key.verify(signature, f"{parts[0]}.{parts[1]}".encode())
        except InvalidSignature as exc:
            raise InvalidAssertion("主張の署名が不正") from exc
        try:
            claims = _Claims.model_validate_json(_b64decode(parts[1]))
        except (ValidationError, ValueError, binascii.Error) as exc:
            raise InvalidAssertion("主張の内容が不正") from exc
        now = int(time.time())
        if claims.iss != ISSUER or claims.aud != AUDIENCE:
            raise InvalidAssertion("主張の発行者・宛先が違う")
        if claims.exp < now - CLOCK_SKEW_S or claims.iat > now + CLOCK_SKEW_S:
            raise InvalidAssertion("主張の期限が切れている")
        if claims.exp - claims.iat > MAX_LIFETIME_S:
            raise InvalidAssertion("主張の有効期間が長すぎる")
        return ExternalIdentity(
            issuer=claims.idp,
            subject=claims.sub,
            email=claims.email,
            email_verified=claims.email_verified,
            name=claims.name,
        )
