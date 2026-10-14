"""テスト用の偽の Google（OIDC プロバイダー）。外部と通信せず、httpx.MockTransport で応答する。

認可の画面（ブラウザが開くもの）は authorize() で代わりに行い、認可コードを発行する。トークンの交換では、
クライアントの資格情報・リダイレクト URI・PKCE を確かめ、認可コードは1回だけ使える（Google と同じ）。
ID トークンは、テストごとに作る RSA の鍵で署名する（合成データだけを使う）。
"""

import base64
import hashlib
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"
AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
JWKS = "https://www.googleapis.com/oauth2/v3/certs"
CLIENT_ID = "test-client.apps.googleusercontent.example"
CLIENT_SECRET = "test-secret-not-real"


def new_key(kid: str) -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": kid, "use": "sig", "alg": "RS256"})


class FakeGoogle:
    def __init__(self) -> None:
        self.key = new_key("google-1")
        self.codes: dict[str, dict[str, Any]] = {}
        self.fail_network = False
        self.requests: list[httpx.Request] = []
        self.metadata: dict[str, Any] = {
            "issuer": "https://accounts.google.com",
            "authorization_endpoint": AUTHORIZE,
            "token_endpoint": TOKEN,
            "jwks_uri": JWKS,
        }
        self._serial = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ------------------------------------------------------------ ブラウザの代わり

    def authorize(self, url: str, **overrides: Any) -> tuple[str, str]:
        """認可の画面で、利用者がアカウントを選んだことにする。（認可コード, state）を返す。

        overrides：ID トークンのクレームの上書き（None を渡すとそのクレームを除く）。
        """
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == AUTHORIZE
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert query["response_type"] == "code"
        assert query["client_id"] == CLIENT_ID
        assert query["code_challenge_method"] == "S256"
        assert set(query["scope"].split()) == {"openid", "email"}
        self._serial += 1
        code = f"code-{self._serial}"
        self.codes[code] = {
            "redirect_uri": query["redirect_uri"],
            "challenge": query["code_challenge"],
            "nonce": query["nonce"],
            "overrides": overrides,
            "used": False,
        }
        return code, query["state"]

    def claims(self, nonce: str, /, **overrides: Any) -> dict[str, Any]:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": "https://accounts.google.com",
            "azp": CLIENT_ID,
            "aud": CLIENT_ID,
            "sub": "100000000000000000001",
            "email": "taro@s.okayama-u.ac.jp",
            "email_verified": True,
            "hd": "s.okayama-u.ac.jp",
            "nonce": nonce,
            "iat": now,
            "exp": now + 3600,
        }
        for key, value in overrides.items():
            if value is None:
                claims.pop(key, None)
            else:
                claims[key] = value
        return claims

    def sign(self, claims: dict[str, Any], key: RSAKey | None = None) -> str:
        signer = key or self.key
        return jwt.encode({"alg": "RS256", "kid": signer.kid, "typ": "JWT"}, claims, signer)

    # ------------------------------------------------------------ Google のエンドポイント

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_network:
            raise httpx.ConnectError("偽の通信の失敗", request=request)
        url = str(request.url)
        if request.method == "GET" and url == DISCOVERY:
            return httpx.Response(200, json=self.metadata)
        if request.method == "GET" and url == JWKS:
            return httpx.Response(200, json=KeySet([self.key]).as_dict(private=False))
        if request.method == "POST" and url == TOKEN:
            return self._token(request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            return httpx.Response(401, json={"error": "invalid_client"})
        grant = self.codes.get(form.get("code", ""))
        if grant is None or grant["used"] or form.get("grant_type") != "authorization_code":
            return httpx.Response(400, json={"error": "invalid_grant"})
        grant["used"] = True
        if form.get("redirect_uri") != grant["redirect_uri"]:
            return httpx.Response(400, json={"error": "redirect_uri_mismatch"})
        verifier = form.get("code_verifier", "")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if challenge != grant["challenge"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        overrides = dict(grant["overrides"])
        id_token = overrides.pop("_id_token", None)
        token = id_token or self.sign(self.claims(grant["nonce"], **overrides))
        return httpx.Response(
            200,
            json={
                "access_token": f"access-{form['code']}",
                "id_token": token,
                "token_type": "Bearer",
                "expires_in": 3599,
                "scope": "openid email",
            },
        )
