"""認証サービスの設定（環境変数から読む）。秘密の値はログ・例外のメッセージに出さない。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from shared.email_domain import parse_allowed_domains

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
# Google の ID トークンの発行者（どちらの表記でも発行される）。利用者の対応付けでは前者にそろえる
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
# OIDC のエンドポイントとして受け付けるホスト（auth_proxy の許可リストと同じ）
GOOGLE_HOSTS = frozenset({"accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"})
DEFAULT_REDIRECT_URI = "http://localhost:8080/auth/callback"


class ConfigError(ValueError):
    """設定が不正（起動を止める）。"""


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    allowed_domains: frozenset[str]
    data_dir: Path
    public_key_file: Path
    session_hours: int = 8
    idle_minutes: int = 120
    discovery_url: str = GOOGLE_DISCOVERY_URL
    issuers: tuple[str, ...] = GOOGLE_ISSUERS
    endpoint_hosts: frozenset[str] = GOOGLE_HOSTS

    def __post_init__(self) -> None:
        parts = urlsplit(self.redirect_uri)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ConfigError("GOOGLE_OAUTH_REDIRECT_URI は http(s):// で始まる URL にしてください")
        if parts.path != "/auth/callback" or parts.query or parts.fragment:
            raise ConfigError("GOOGLE_OAUTH_REDIRECT_URI のパスは /auth/callback にしてください")
        if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1"):
            raise ConfigError("http の GOOGLE_OAUTH_REDIRECT_URI は localhost だけで使えます")
        if not 1 <= self.session_hours <= 24 * 7:
            raise ConfigError("MONGAI_SESSION_HOURS は 1〜168 にしてください")
        if not 5 <= self.idle_minutes <= self.session_hours * 60:
            raise ConfigError(
                "MONGAI_SESSION_IDLE_MINUTES は 5 分以上、セッションの期間以下にしてください"
            )

    @property
    def configured(self) -> bool:
        """Google のクライアントと、許可するドメインが設定されているか（無ければログインできない）。"""
        return bool(self.client_id and self.client_secret and self.allowed_domains)

    @property
    def origin(self) -> str:
        """利用者がアクセスする URL のオリジン（コールバックの URL から決める。要求の Host は使わない）。"""
        parts = urlsplit(self.redirect_uri)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def host(self) -> str:
        return urlsplit(self.redirect_uri).netloc

    @property
    def secure_cookies(self) -> bool:
        return urlsplit(self.redirect_uri).scheme == "https"


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"{name} は整数にしてください") from exc


def load_settings() -> Settings:
    return Settings(
        client_id=os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip(),
        client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
        redirect_uri=os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
        or DEFAULT_REDIRECT_URI,
        allowed_domains=parse_allowed_domains(os.environ.get("MONGAI_ALLOWED_EMAIL_DOMAINS", "")),
        data_dir=Path(os.environ.get("AUTHN_DATA_DIR", "/data/authn")),
        public_key_file=Path(os.environ.get("AUTHN_PUBLIC_KEY_FILE", "/keys/authn.json")),
        session_hours=_int("MONGAI_SESSION_HOURS", 8),
        idle_minutes=_int("MONGAI_SESSION_IDLE_MINUTES", 120),
    )
