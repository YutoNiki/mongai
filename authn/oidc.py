"""Google の OpenID Connect（認可コードフロー＋PKCE）と、ID トークンの検証。

- 認可の URL の組み立て・state・nonce・PKCE の値の生成と、ID トークンのクレームの検証（iss・aud・exp・iat・
  nonce・azp・at_hash）には Authlib を、署名の検証（JWKS）には Authlib が依存する joserfc を使う
- Google との HTTP 通信は httpx で行う（環境変数 HTTPS_PROXY の auth_proxy 経由）。Authlib の httpx 連携は
  1.8 で非推奨になったため使わない
- 許可するのは、確認済みのメールアドレス（email_verified が true）と、組織のドメイン（hd）の両方が、
  許可したドメインかそのサブドメインに含まれる利用者だけ。両者が同じであることまでは求めない
- 認可の要求に付ける hd（* ＝ Google Workspace のアカウント全般）はアカウントの選択の補助で、許可の判定には
  使わない（ID トークンの hd で判定する）
- ID トークン・認可コード・アクセストークンはログに出さない。拒否したときは、理由と、安全な属性
  （クレームの名前、hd の値、メールのドメイン部、email_verified）だけをログに出す
"""

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from authlib.common.security import generate_token
from authlib.oauth2.rfc6749.parameters import prepare_grant_uri
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from authlib.oidc.core import CodeIDToken
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from authn.config import Settings
from shared.email_domain import domain_allowed, email_allowed, email_domain

log = logging.getLogger("authn.oidc")

SCOPE = "openid email"
LEEWAY_S = 60
# ID トークンの発行（iat）から受け付けるまでの上限
MAX_TOKEN_AGE_S = 10 * 60
CACHE_S = 60 * 60
HTTP_TIMEOUT_S = 10.0


class LoginError(Exception):
    """ログインを受け付けない。code は利用者の画面に出す分類（詳細はログだけに出す）。

    - denied：許可していないアカウント（ドメイン・hd・確認されていないメールアドレス）
    - failed：トークンが不正・認可コードが使えない（再利用など）
    - unavailable：Google と通信できない
    """

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str  # 正規化した発行者（https://accounts.google.com）
    subject: str
    email: str


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    nonce: str
    code_verifier: str


def _check_host(url: object, hosts: frozenset[str]) -> str:
    if not isinstance(url, str):
        raise LoginError("unavailable", "OIDC の設定にエンドポイントが無い")
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in hosts:
        raise LoginError("unavailable", f"想定外の OIDC のエンドポイント：{parts.hostname}")
    return url


def _kid(token: str) -> str | None:
    """署名を確かめる前のヘッダーの kid（鍵を選ぶためだけに使う）。"""
    try:
        part = token.split(".")[0]
        header = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, IndexError):
        return None
    kid = header.get("kid") if isinstance(header, dict) else None
    return kid if isinstance(kid, str) else None


def safe_claim_summary(claims: dict[str, Any]) -> dict[str, Any]:
    """調査用にログへ出してよい属性だけ（sub・メールのローカル部・トークンは含めない）。"""
    email = claims.get("email")
    return {
        "claims": sorted(claims),
        "hd": claims.get("hd") if isinstance(claims.get("hd"), str) else None,
        "email_domain": email_domain(email) if isinstance(email, str) else None,
        "email_verified": claims.get("email_verified"),
    }


class GoogleOIDC:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._metadata: tuple[float, dict[str, str]] | None = None
        self._jwks: tuple[float, KeySet] | None = None

    def _client(self) -> httpx.AsyncClient:
        # プロキシは環境変数（HTTPS_PROXY=http://auth_proxy:3128）に従う。リダイレクトは追わない
        return httpx.AsyncClient(
            transport=self._transport, timeout=HTTP_TIMEOUT_S, follow_redirects=False
        )

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise LoginError(
                "unavailable", f"Google と通信できない（{type(exc).__name__}）"
            ) from exc
        if response.status_code != 200:
            raise LoginError("unavailable", f"Google の応答が {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise LoginError("unavailable", "Google の応答が JSON でない") from exc
        if not isinstance(data, dict):
            raise LoginError("unavailable", "Google の応答の形式が違う")
        return data

    async def metadata(self) -> dict[str, str]:
        """OIDC Discovery の設定（1時間キャッシュ）。エンドポイントは許可したホストだけを受け付ける。"""
        if self._metadata and time.monotonic() - self._metadata[0] < CACHE_S:
            return self._metadata[1]
        data = await self._get_json(self.settings.discovery_url)
        hosts = self.settings.endpoint_hosts
        if data.get("issuer") not in self.settings.issuers:
            raise LoginError("unavailable", "OIDC の設定の issuer が想定と違う")
        metadata = {
            key: _check_host(data.get(key), hosts)
            for key in ("authorization_endpoint", "token_endpoint", "jwks_uri")
        }
        self._metadata = (time.monotonic(), metadata)
        return metadata

    async def _keys(self, *, refresh: bool = False) -> KeySet:
        if not refresh and self._jwks and time.monotonic() - self._jwks[0] < CACHE_S:
            return self._jwks[1]
        data = await self._get_json((await self.metadata())["jwks_uri"])
        try:
            keys = KeySet.import_key_set(data)  # type: ignore[arg-type]
        except (JoseError, ValueError, TypeError, KeyError) as exc:
            raise LoginError("unavailable", "Google の公開鍵を読めない") from exc
        self._jwks = (time.monotonic(), keys)
        return keys

    async def authorization_request(self) -> AuthorizationRequest:
        endpoint = (await self.metadata())["authorization_endpoint"]
        state = generate_token(43)
        nonce = generate_token(43)
        code_verifier = generate_token(64)
        extra: dict[str, str] = {
            "nonce": nonce,
            "code_challenge": create_s256_code_challenge(code_verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
            # Google Workspace のアカウントを選びやすくする補助（許可の判定には使わない）。
            # 特定のドメイン（例：okayama-u.ac.jp）を指定すると、Google の画面がメールアドレスの末尾を
            # そのドメインに固定し、別の組織のサブドメイン（s.okayama-u.ac.jp の学生など）を入力できなくなる
            "hd": "*",
        }
        url = prepare_grant_uri(
            endpoint,
            client_id=self.settings.client_id,
            response_type="code",
            redirect_uri=self.settings.redirect_uri,
            scope=SCOPE,
            state=state,
            **extra,
        )
        return AuthorizationRequest(str(url), state, nonce, code_verifier)

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        """認可コードをトークンに交換する（クライアントシークレットはこの要求の本文にだけ使う）。"""
        endpoint = (await self.metadata())["token_endpoint"]
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.redirect_uri,
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
            "code_verifier": code_verifier,
        }
        try:
            async with self._client() as client:
                response = await client.post(
                    endpoint, data=form, headers={"Accept": "application/json"}
                )
        except httpx.HTTPError as exc:
            raise LoginError(
                "unavailable", f"Google と通信できない（{type(exc).__name__}）"
            ) from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code != 200 or not isinstance(data, dict):
            error = data.get("error") if isinstance(data, dict) else None
            code_ = "failed" if response.status_code in (400, 401) else "unavailable"
            raise LoginError(code_, f"認可コードを交換できない（{response.status_code} {error}）")
        if not isinstance(data.get("id_token"), str):
            raise LoginError("failed", "トークンの応答に ID トークンが無い")
        return data

    async def verify_id_token(
        self, id_token: str, *, nonce: str, access_token: str | None = None
    ) -> VerifiedIdentity:
        try:
            keys = await self._keys()
            if _kid(id_token) not in {k.kid for k in keys.keys}:
                # Google が鍵を入れ替えた直後かもしれないので、公開鍵を取り直す
                keys = await self._keys(refresh=True)
            token = jwt.decode(id_token, keys, algorithms=["RS256"])
        except (JoseError, ValueError) as exc:
            raise LoginError(
                "failed", f"ID トークンの署名・形式が不正（{type(exc).__name__}）"
            ) from exc

        claims: dict[str, Any] = dict(token.claims)
        params: dict[str, Any] = {"nonce": nonce, "client_id": self.settings.client_id}
        if access_token:
            params["access_token"] = access_token
        options: dict[str, Any] = {
            "iss": {"essential": True, "values": list(self.settings.issuers)},
            "aud": {"essential": True, "value": self.settings.client_id},
            "sub": {"essential": True},
            "nonce": {"essential": True},
        }
        try:
            CodeIDToken(claims, dict(token.header), options, params).validate(leeway=LEEWAY_S)
        except JoseError as exc:
            raise LoginError("failed", f"ID トークンのクレームが不正（{exc}）") from exc
        iat = claims.get("iat")
        if not isinstance(iat, int) or iat < time.time() - MAX_TOKEN_AGE_S - LEEWAY_S:
            raise LoginError("failed", "ID トークンが古い")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise LoginError("failed", "ID トークンの sub が不正")

        allowed = self.settings.allowed_domains
        email = claims.get("email")
        hd = claims.get("hd")
        reason = None
        if claims.get("email_verified") is not True:
            reason = "メールアドレスが確認されていない"
        elif not isinstance(email, str) or not email_allowed(email, allowed):
            reason = "メールアドレスのドメインが許可の範囲外"
        elif not isinstance(hd, str):
            reason = "組織のドメイン（hd）が無い（大学の Google Workspace のアカウントではない）"
        elif not domain_allowed(hd, allowed):
            reason = "組織のドメイン（hd）が許可の範囲外"
        if reason is not None:
            log.warning("ログインを拒否：%s %s", reason, safe_claim_summary(claims))
            raise LoginError("denied", reason)
        assert isinstance(email, str)
        return VerifiedIdentity(self.settings.issuers[0], subject, email.strip().lower())
