"""開発用のモック認証サービス。本物の認証サービス（authn）と同じ形の、署名つきの主張を web に返す。

- 起動のたびに Ed25519 の鍵を作り、公開鍵を /keys/devauth.json に書く（core が読み取り専用で読む）。
  鍵の ID は dev- で始まり、本番の構成（MONGAI_ENV=production）の core は受け付けない
- /auth/dev-login?user=alice で、その名前の利用者としてログインした状態にする（Cookie）。
  /auth/dev-logout でゲストに戻る。パスワードは無い（開発用のため。本番では使わない）
- web（nginx）の auth_request から /auth/verify を呼ぶ。Cookie が無ければゲスト（主張なし）、
  有効なら主張をヘッダーで返し、不正なら 401
- 学外とは通信しない（internal_net だけに所属する）
"""

import base64
import json
import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse

COOKIE = "mongai_dev_user"
USER = re.compile(r"[a-z0-9_-]{1,32}")
LIFETIME_S = 300
IDP = "https://devauth.invalid"
EMAIL_DOMAIN = os.environ.get("DEVAUTH_EMAIL_DOMAIN", "example.ac.jp")

_key = Ed25519PrivateKey.generate()
_kid = f"dev-{secrets.token_hex(4)}"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def write_public_key() -> None:
    path = Path(os.environ.get("DEVAUTH_PUBLIC_KEY_FILE", "/keys/devauth.json"))
    public = _key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"kid": _kid, "public_key": base64.b64encode(public).decode()}))
    tmp.chmod(0o644)
    tmp.replace(path)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    write_public_key()
    yield


app = FastAPI(
    title="MongAI devauth", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)


def assertion(user: str) -> str:
    now = int(time.time())
    claims = {
        "iss": "mongai-authn",
        "aud": "mongai-core",
        "iat": now,
        "exp": now + LIFETIME_S,
        "idp": IDP,
        "sub": user,
        "email": f"{user}@{EMAIL_DOMAIN}",
        "email_verified": True,
        "name": user,
    }
    header = _b64(json.dumps({"alg": "EdDSA", "typ": "JWT", "kid": _kid}).encode())
    payload = _b64(json.dumps(claims).encode())
    return f"{header}.{payload}.{_b64(_key.sign(f'{header}.{payload}'.encode()))}"


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/verify")
def verify(request: Request) -> Response:
    user = request.cookies.get(COOKIE)
    if user is None:
        return Response(status_code=200)  # ゲスト
    if not USER.fullmatch(user):
        return Response(status_code=401)  # 不正なセッションはゲストにしない
    return Response(status_code=200, headers={"X-MongAI-Assertion": assertion(user)})


@app.get("/auth/dev-login")
def dev_login(user: str) -> Response:
    if not USER.fullmatch(user):
        return Response("user は英小文字・数字・-・_ の32文字以内にしてください", status_code=422)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE, user, httponly=True, samesite="lax", path="/")
    return response


@app.get("/auth/dev-logout")
def dev_logout() -> Response:
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE, path="/")
    return response
