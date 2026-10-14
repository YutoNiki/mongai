"""認証サービス（authn）の API。web（nginx）の /auth/ から中継される。

- GET  /auth/login：Google のログイン画面へ移る（state・nonce・PKCE を作り、ブラウザに結び付けて保存する）
- GET  /auth/callback：Google から戻ったところ。state を確かめ、認可コードを交換し、ID トークンを検証して、
  許可した利用者ならログインのセッションを作る。結果にかかわらず、アプリの画面（設定したオリジンの中）に戻す
- GET  /auth/verify：nginx の auth_request から呼ぶ（ブラウザからは呼べない。nginx で塞ぐ）
  - Cookie なし → 200（主張なし。ゲスト）
  - 有効なセッション → 200 と、署名つきの主張（ヘッダー X-MongAI-Assertion）
  - Cookie があるが不正・失効・期限切れ → 401（ゲストにしない）
  - セッションのストアを読めない → 503（nginx は 503 auth_unavailable を返す。認証済みにもゲストにもしない）
- POST /auth/logout：セッションをサーバー側で消し、Cookie を消す（同じオリジンからの要求だけ）

利用者の画面に出すエラーは分類（?auth_error=…）だけで、トークン・認可コードは URL・ログに出さない。
"""

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse

from authn.config import Settings, load_settings
from authn.keys import SigningKey, ensure_signing_key
from authn.oidc import GoogleOIDC, LoginError
from authn.store import FLOW_LIFETIME_S, SessionStore, StoreError, new_secret
from shared.email_domain import email_allowed

log = logging.getLogger("authn")

ASSERTION_HEADER = "X-MongAI-Assertion"
# Cookie の値の形式（secrets.token_urlsafe(32)）。形式の違う値はストアに問い合わせずに 401
_SECRET = re.compile(r"[A-Za-z0-9_-]{43}")
_STATE = re.compile(r"[A-Za-z0-9_-]{20,128}")
# ログイン後に戻す画面（アプリの中のパスだけ。// で始まる URL・/api/・/auth/ には戻さない）
_NEXT = re.compile(r"/(?!/)(?!api/)(?!auth/)[A-Za-z0-9/_.-]{0,200}")
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def safe_next(value: str | None) -> str:
    if value and _NEXT.fullmatch(value) and ".." not in value:
        return value
    return "/"


@dataclass
class Runtime:
    settings: Settings
    store: SessionStore
    key: SigningKey
    oidc: GoogleOIDC

    @property
    def session_cookie(self) -> str:
        # HTTPS では __Host- を付け、Secure・Path=/・Domain なしを強制する
        return "__Host-mongai_session" if self.settings.secure_cookies else "mongai_session"

    @property
    def flow_cookie(self) -> str:
        return "__Host-mongai_login" if self.settings.secure_cookies else "mongai_login"

    def set_cookie(self, response: Response, name: str, value: str, max_age: int) -> None:
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            path="/",
            httponly=True,
            samesite="lax",
            secure=self.settings.secure_cookies,
        )

    def clear_cookie(self, response: Response, name: str) -> None:
        response.delete_cookie(
            name, path="/", httponly=True, samesite="lax", secure=self.settings.secure_cookies
        )


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        config = settings or load_settings()
        key = ensure_signing_key(config.data_dir, config.public_key_file)
        store = SessionStore(config.data_dir / "sessions.db")
        app.state.runtime = Runtime(config, store, key, GoogleOIDC(config, transport))
        if not config.configured:
            log.warning(
                "Google のクライアント（GOOGLE_OAUTH_CLIENT_ID・GOOGLE_OAUTH_CLIENT_SECRET）か、"
                "許可するドメイン（MONGAI_ALLOWED_EMAIL_DOMAINS）が未設定。ログインできず、全員がゲストになる"
            )
        log.info("認証サービスを起動（鍵 %s、コールバック %s）", key.kid, config.redirect_uri)
        yield

    app = FastAPI(
        title="SI-NSA authn", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    def rt() -> Runtime:
        runtime: Runtime | None = getattr(app.state, "runtime", None)
        if runtime is None:
            raise RuntimeError("認証サービスが起動していない")
        return runtime

    @app.middleware("http")
    async def no_store(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers.update(NO_STORE)
        return response

    def back_to_app(r: Runtime, error: str | None = None, path: str = "/") -> RedirectResponse:
        target = f"{path}?{urlencode({'auth_error': error})}" if error else path
        response = RedirectResponse(target, status_code=303)
        r.clear_cookie(response, r.flow_cookie)
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        # セッションのストアを読めることを確かめる（読めなければ 500）
        rt().store.get_session("x" * 43, idle_s=60)
        return {"status": "ok"}

    @app.get("/auth/login")
    async def login(request: Request, next: str | None = None) -> Response:
        r = rt()
        next_path = safe_next(next)
        # Cookie は設定したオリジン（例：http://localhost:8080）に結び付くため、別の名前（127.0.0.1 など）で
        # 開かれたら、設定したオリジンでログインをやり直させる（転送先は設定値だけで、要求の Host は使わない）
        if request.headers.get("host", "").lower() != r.settings.host.lower():
            query = f"?{urlencode({'next': next_path})}" if next_path != "/" else ""
            return RedirectResponse(f"{r.settings.origin}/auth/login{query}", status_code=303)
        if not r.settings.configured:
            return back_to_app(r, "not_configured")
        try:
            auth = await r.oidc.authorization_request()
        except LoginError as exc:
            log.warning("ログインを開始できない：%s", exc.reason)
            return back_to_app(r, "unavailable")
        browser_secret = new_secret()
        try:
            r.store.create_flow(
                auth.state, browser_secret, auth.nonce, auth.code_verifier, next_path
            )
        except StoreError:
            log.exception("ログインの状態を保存できない")
            return back_to_app(r, "unavailable")
        response = RedirectResponse(auth.url, status_code=303)
        r.set_cookie(response, r.flow_cookie, browser_secret, FLOW_LIFETIME_S)
        return response

    @app.get("/auth/callback")
    async def callback(
        request: Request,
        state: str | None = None,
        code: str | None = None,
        error: str | None = None,
    ) -> Response:
        r = rt()
        if not r.settings.configured:
            return back_to_app(r, "not_configured")
        browser_secret = request.cookies.get(r.flow_cookie)
        if (
            not state
            or not _STATE.fullmatch(state)
            or not browser_secret
            or not _SECRET.fullmatch(browser_secret)
        ):
            log.info("ログインの状態が無いコールバック（直接のアクセス・別のブラウザ・期限切れ）")
            return back_to_app(r, "expired")
        try:
            flow = r.store.take_flow(state, browser_secret)
        except StoreError:
            log.exception("ログインの状態を読めない")
            return back_to_app(r, "unavailable")
        if flow is None:
            log.info("state が一致しない・使用済み・期限切れのコールバック")
            return back_to_app(r, "expired")
        if error is not None:
            # 利用者がキャンセルした（access_denied）など。error の値は Google の定めた分類だけを記録する
            log.info("Google がエラーを返した：%s", error[:64])
            return back_to_app(r, "cancelled")
        if not code or len(code) > 2048:
            return back_to_app(r, "failed")
        try:
            tokens = await r.oidc.exchange_code(code, flow.code_verifier)
            access_token = tokens.get("access_token")
            identity = await r.oidc.verify_id_token(
                tokens["id_token"],
                nonce=flow.nonce,
                access_token=access_token if isinstance(access_token, str) else None,
            )
        except LoginError as exc:
            log.warning("ログインを受け付けない（%s）：%s", exc.code, exc.reason)
            return back_to_app(r, exc.code)
        # セッションの固定化を防ぐ：ログインのたびに新しい ID を作り、前のセッションは消す
        old = request.cookies.get(r.session_cookie)
        try:
            if old and _SECRET.fullmatch(old):
                r.store.delete_session(old)
            session_id, _ = r.store.create_session(
                identity.issuer,
                identity.subject,
                identity.email,
                lifetime_s=r.settings.session_hours * 3600,
            )
        except StoreError:
            log.exception("セッションを保存できない")
            return back_to_app(r, "unavailable")
        log.info("ログイン（メールのドメイン %s）", identity.email.rpartition("@")[2])
        response = back_to_app(r, path=flow.next_path)
        r.set_cookie(response, r.session_cookie, session_id, r.settings.session_hours * 3600)
        return response

    @app.get("/auth/verify")
    def verify(request: Request) -> Response:
        r = rt()
        session_id = request.cookies.get(r.session_cookie)
        if session_id is None:
            return Response(status_code=200)  # ゲスト
        if not r.settings.configured or not _SECRET.fullmatch(session_id):
            return Response(status_code=401)
        try:
            session = r.store.get_session(session_id, idle_s=r.settings.idle_minutes * 60)
        except StoreError:
            log.exception("セッションを確かめられない（認証済みとはみなさない）")
            return Response(status_code=503)
        if session is None:
            return Response(status_code=401)
        # 許可するドメインの設定を狭めたら、既存のセッションもその場で無効にする
        if not email_allowed(session.email, r.settings.allowed_domains):
            try:
                r.store.delete_session(session_id)
            except StoreError:
                log.exception("範囲外のセッションを消せない")
            return Response(status_code=401)
        token = r.key.sign_assertion(
            idp=session.issuer, subject=session.subject, email=session.email
        )
        return Response(status_code=200, headers={ASSERTION_HEADER: token})

    def same_origin(r: Runtime, request: Request) -> bool:
        site = request.headers.get("sec-fetch-site")
        if site is not None and site != "same-origin":
            return False
        return request.headers.get("origin") == r.settings.origin

    @app.post("/auth/logout")
    def logout(request: Request) -> Response:
        r = rt()
        if not same_origin(r, request):
            return Response("ほかのサイトからの要求は受け付けません", status_code=403)
        session_id = request.cookies.get(r.session_cookie)
        status = 204
        if session_id and _SECRET.fullmatch(session_id):
            try:
                r.store.delete_session(session_id)
            except StoreError:
                log.exception("セッションを消せない")
                status = 503
        response = Response(status_code=status)
        if status == 204:  # 消せなかったときは Cookie を残し、もう一度ログアウトできるようにする
            r.clear_cookie(response, r.session_cookie)
            r.clear_cookie(response, r.flow_cookie)
        return response

    return app


app = create_app()
