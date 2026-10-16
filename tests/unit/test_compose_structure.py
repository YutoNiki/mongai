"""compose.yaml の静的検査（SPEC 2.2・7.1）。

実際の通信の検証は tests/security/run.sh で行う。ここでは、設定ファイルの変更で
分離が崩れていないかを、コンテナを起動せずに確認する。
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_SERVICES = {
    "web",
    "core",
    "worker",
    "gateway",
    "searcher",
    "redis",
    "grobid",
    "oitoma_proxy",
    "egress_proxy",
    "authn",
    "auth_proxy",
}

EXPECTED_NETWORKS = {
    "web": {"ui_net", "internal_net", "auth_net"},
    "core": {"internal_net"},
    "worker": {"internal_net"},
    "gateway": {"internal_net", "gateway_net"},
    "searcher": {"gateway_net", "egress_net"},
    "redis": {"internal_net"},
    "grobid": {"internal_net"},
    "oitoma_proxy": {"internal_net", "oitoma_net"},
    "egress_proxy": {"egress_net", "outbound_net"},
    "authn": {"auth_net", "auth_egress_net"},
    "auth_proxy": {"auth_egress_net", "auth_outbound_net"},
}

PRIVATE_VOLUMES = {"data_private", "lancedb", "app_db", "audit_log"}
# 認証サービスの秘密鍵・セッション（authn だけがマウントする）
AUTHN_PRIVATE_VOLUMES = {"authn_data"}


def load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return load("compose.yaml")


def test_project_name(compose: dict[str, Any]) -> None:
    assert compose["name"] == "mongai"


def test_services(compose: dict[str, Any]) -> None:
    assert set(compose["services"]) == EXPECTED_SERVICES


def test_internal_networks(compose: dict[str, Any]) -> None:
    networks = compose["networks"]
    for name in ("internal_net", "gateway_net", "egress_net", "auth_net", "auth_egress_net"):
        assert networks[name].get("internal") is True, name
    assert set(networks) == {
        "ui_net",
        "internal_net",
        "oitoma_net",
        "gateway_net",
        "egress_net",
        "outbound_net",
        "auth_net",
        "auth_egress_net",
        "auth_outbound_net",
    }


@pytest.mark.parametrize("service", sorted(EXPECTED_SERVICES))
def test_service_networks(compose: dict[str, Any], service: str) -> None:
    assert set(compose["services"][service]["networks"]) == EXPECTED_NETWORKS[service]


def test_only_web_publishes_port_on_localhost(compose: dict[str, Any]) -> None:
    for name, svc in compose["services"].items():
        if name == "web":
            assert svc["ports"] == ["127.0.0.1:8080:8080"]
        else:
            assert "ports" not in svc, name
            assert "network_mode" not in svc, name


def test_core_and_searcher_do_not_share_network(compose: dict[str, Any]) -> None:
    services = compose["services"]
    for core_side in ("core", "worker", "web", "redis", "grobid"):
        shared = set(services[core_side]["networks"]) & set(services["searcher"]["networks"])
        assert not shared, (core_side, shared)


def _mounted_volume_names(svc: dict[str, Any]) -> set[str]:
    names = set()
    for vol in svc.get("volumes", []):
        source = vol.split(":", 1)[0] if isinstance(vol, str) else vol.get("source", "")
        names.add(source)
    return names


def test_searcher_has_no_private_volume(compose: dict[str, Any]) -> None:
    searcher = compose["services"]["searcher"]
    assert not _mounted_volume_names(searcher) & PRIVATE_VOLUMES
    assert "volumes" not in searcher


@pytest.mark.parametrize(
    "service", ["web", "gateway", "searcher", "redis", "grobid", "authn", "auth_proxy"]
)
def test_private_data_only_in_core(compose: dict[str, Any], service: str) -> None:
    allowed = {"gateway": {"audit_log"}}.get(service, set())
    mounted = _mounted_volume_names(compose["services"][service]) & PRIVATE_VOLUMES
    assert mounted <= allowed, (service, mounted)


def test_external_access_only_through_dedicated_proxies(compose: dict[str, Any]) -> None:
    """学外に出られるネットワーク（internal でないもの）に繋がるのは、web の入口と3つのプロキシだけ。"""
    external = {n for n, cfg in compose["networks"].items() if not (cfg or {}).get("internal")}
    assert external == {"ui_net", "oitoma_net", "outbound_net", "auth_outbound_net"}
    for name, svc in compose["services"].items():
        joined = set(svc["networks"]) & external
        expected = {
            "web": {"ui_net"},
            "oitoma_proxy": {"oitoma_net"},
            "egress_proxy": {"outbound_net"},
            "auth_proxy": {"auth_outbound_net"},
        }.get(name, set())
        assert joined == expected, (name, joined)


def test_authn_is_isolated_from_core_side(compose: dict[str, Any]) -> None:
    """authn は core・worker・gateway・searcher とネットワークを共有しない（web とだけ話す）。"""
    services = compose["services"]
    authn = set(services["authn"]["networks"])
    for other in ("core", "worker", "gateway", "searcher", "redis", "grobid"):
        assert not authn & set(services[other]["networks"]), other
    assert not set(services["auth_proxy"]["networks"]) & set(services["web"]["networks"])


def test_auth_secrets_stay_in_authn(compose: dict[str, Any]) -> None:
    """署名の秘密鍵・セッションのボリュームと、Google のクライアントシークレットは authn にだけ渡す。"""
    for name, svc in compose["services"].items():
        mounted = _mounted_volume_names(svc)
        if name == "authn":
            assert mounted == {"authn_data", "authn_public"}
        else:
            assert not mounted & AUTHN_PRIVATE_VOLUMES, name
        env = svc.get("environment", {})
        has_secret = isinstance(env, dict) and "GOOGLE_OAUTH_CLIENT_SECRET" in env
        assert has_secret == (name == "authn"), name
    assert "authn_public:/keys/authn:ro" in compose["services"]["core"]["volumes"]
    assert "authn_public" not in str(compose["services"]["worker"].get("volumes", []))
    assert "authn_public" not in str(compose["services"]["searcher"].get("volumes", []))


def test_authn_uses_auth_proxy_only(compose: dict[str, Any]) -> None:
    env = compose["services"]["authn"]["environment"]
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://auth_proxy:3128"
    assert "OITOMA_API_KEY" not in env
    core_env = compose["services"]["core"]["environment"]
    assert core_env["MONGAI_AUTH_PUBLIC_KEY_FILE"] == "/keys/authn/authn.json"


def test_auth_allowlist_is_google_oidc_only() -> None:
    hosts = (ROOT / "config/proxy/auth_allowlist.txt").read_text(encoding="utf-8").split()
    assert set(hosts) == {"accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"}
    assert not any(h.startswith(".") or "*" in h for h in hosts)  # ワイルドカードを使わない
    conf = (ROOT / "config/proxy/auth_squid.conf").read_text(encoding="utf-8")
    rules = [line for line in conf.splitlines() if line.startswith("http_access")]
    assert rules == [
        "http_access deny !localnet",
        "http_access deny !CONNECT",
        "http_access deny !SSL_ports",
        "http_access allow CONNECT allowed_hosts",
        "http_access deny all",
    ]
    assert 'acl allowed_hosts dstdomain "/etc/squid/auth_allowlist.txt"' in conf
    egress = (ROOT / "config/proxy/egress_allowlist.txt").read_text(encoding="utf-8").split()
    assert not set(hosts) & set(egress)  # 書誌検索用のプロキシとは分ける


def test_audit_log_read_only_in_core(compose: dict[str, Any]) -> None:
    assert "audit_log:/data/audit:ro" in compose["services"]["core"]["volumes"]


@pytest.mark.parametrize("service", sorted(EXPECTED_SERVICES))
def test_hardening(compose: dict[str, Any], service: str) -> None:
    svc = compose["services"][service]
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert "healthcheck" in svc


@pytest.mark.parametrize("service", sorted(EXPECTED_SERVICES))
def test_depends_on_waits_for_healthy(compose: dict[str, Any], service: str) -> None:
    for dep, cond in compose["services"][service].get("depends_on", {}).items():
        assert cond == {"condition": "service_healthy"}, (service, dep)


def test_images_pinned_by_digest(compose: dict[str, Any]) -> None:
    for name, svc in compose["services"].items():
        if "build" in svc:
            continue
        assert "@sha256:" in svc["image"], name


def test_dockerfile_base_images_pinned() -> None:
    for path in (ROOT / "docker").rglob("Dockerfile"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ARG ") and "_IMAGE=" in line:
                assert "@sha256:" in line, (path, line)


def test_api_key_only_for_oitoma_users(compose: dict[str, Any]) -> None:
    for name, svc in compose["services"].items():
        env = svc.get("environment", {})
        has_key = isinstance(env, dict) and "OITOMA_API_KEY" in env
        assert has_key == (name in {"core", "worker", "gateway"}), name


def test_searcher_uses_egress_proxy_only(compose: dict[str, Any]) -> None:
    env = compose["services"]["searcher"]["environment"]
    assert env["HTTPS_PROXY"] == "http://egress_proxy:3128"
    assert "OITOMA_API_KEY" not in env


def test_egress_allowlist_matches_spec() -> None:
    hosts = (ROOT / "config/proxy/egress_allowlist.txt").read_text(encoding="utf-8").split()
    assert set(hosts) == {
        "api.crossref.org",
        "doi.org",
        "api.openalex.org",
        "eutils.ncbi.nlm.nih.gov",
        "export.arxiv.org",
        "api.semanticscholar.org",
        "cir.nii.ac.jp",
        "api.jstage.jst.go.jp",
    }
    assert "ironical.okayama-u.ac.jp" not in hosts


def test_oitoma_proxy_allows_only_oitoma() -> None:
    conf = (ROOT / "config/proxy/oitoma_squid.conf").read_text(encoding="utf-8")
    acl_lines = [line for line in conf.splitlines() if line.startswith("acl oitoma_host")]
    assert acl_lines == ["acl oitoma_host dstdomain ironical.okayama-u.ac.jp"]
    assert conf.strip().count("http_access allow") == 1
    assert "http_access deny all" in conf


def test_dev_and_test_overrides_do_not_open_network() -> None:
    for name in ("compose.dev.yaml", "compose.test.yaml", "compose.live.yaml"):
        data = load(name)
        for svc_name, svc in data["services"].items():
            assert "ports" not in svc, (name, svc_name)
            nets = set(svc.get("networks", []))
            assert nets <= {"internal_net"}, (name, svc_name, nets)
        assert "networks" not in data, name


def test_core_runs_as_production_without_dev_auth(compose: dict[str, Any]) -> None:
    """本番相当の構成では開発用の鍵を受け付けず（MONGAI_ENV=production）、devauth を含めない。"""
    assert compose["services"]["core"]["environment"]["MONGAI_ENV"] == "production"
    assert "devauth" not in compose["services"]
    assert "devauth" not in yaml.safe_dump(compose)


def test_devauth_is_dev_only_and_isolated() -> None:
    """開発用のモック認証サービスは internal_net だけに所属し、論文・DB・監査ログをマウントしない。"""
    dev = load("compose.dev.yaml")["services"]["devauth"]
    assert dev["networks"] == ["internal_net"] and "ports" not in dev
    sources = {str(v).split(":")[0] for v in dev.get("volumes", [])}
    assert not sources & PRIVATE_VOLUMES
    assert dev["read_only"] is True and dev["cap_drop"] == ["ALL"]


def test_nginx_never_forwards_browser_assertions() -> None:
    """web は /api/ への要求で、ブラウザから来た主張のヘッダーを消すか、認証サービスの値で上書きする。"""
    conf = (ROOT / "docker" / "web" / "nginx.conf").read_text(encoding="utf-8")
    api = conf[conf.index("location /api/") :]
    assert "include /etc/nginx/mongai-auth/api.conf;" in api.split("}")[0]
    for kind in ("authn", "dev"):
        api_conf = (ROOT / "docker" / "web" / "auth" / kind / "api.conf").read_text(
            encoding="utf-8"
        )
        assert "auth_request /_auth;" in api_conf
        assert "proxy_set_header X-MongAI-Assertion $mongai_assertion;" in api_conf
        assert "error_page 401 = @mongai_invalid_session;" in api_conf
        server = (ROOT / "docker" / "web" / "auth" / kind / "server.conf").read_text(
            encoding="utf-8"
        )
        assert "location = /auth/verify {\n    return 404;\n}" in server
        assert "proxy_pass_request_body off;" in server
    assert not (ROOT / "docker" / "web" / "auth" / "none").exists()
    # 401・503 の JSON の応答は、本番と開発用で同じ定義を使う（1か所だけに置く）
    assert "include /etc/nginx/mongai-auth-errors.conf;" in conf
    errors = (ROOT / "docker" / "web" / "auth-errors.conf").read_text(encoding="utf-8")
    assert 'return 401 \'{"detail":"invalid_session"}\';' in errors
    assert 'return 503 \'{"detail":"auth_unavailable"}\';' in errors


def test_web_csp_allows_fonts_only_from_self() -> None:
    """書体は自前で配信する（CLAUDE.md 最優先ルール5、docs/typography.md）。CSP の font-src は 'self' だけ。"""
    conf = (ROOT / "docker" / "web" / "nginx.conf").read_text(encoding="utf-8")
    policies = re.findall(r'add_header Content-Security-Policy "([^"]+)"', conf)
    assert policies
    for policy in policies:
        directives = dict(d.strip().split(" ", 1) for d in policy.split(";") if d.strip())
        assert directives["font-src"] == "'self'"
        assert directives["default-src"] == "'self'"
        for host in ("http", "googleapis", "gstatic"):
            assert host not in policy


def test_production_web_image_uses_authn() -> None:
    """本番のイメージは authn の設定を使い、devauth の設定を含めない（devauth は compose.dev.yaml だけ）。"""
    dockerfile = (ROOT / "docker" / "web" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY docker/web/auth/authn/ /etc/nginx/mongai-auth/" in dockerfile
    assert "auth/dev" not in dockerfile
    server = (ROOT / "docker" / "web" / "auth" / "authn" / "server.conf").read_text(
        encoding="utf-8"
    )
    assert "devauth" not in server
    callback = server[server.index("location = /auth/callback") :].split("\n}\n")[0]
    assert "access_log off;" in callback  # 認可コードをログに残さない
    auth = server[server.index("location /auth/ {") :].split("\n}\n")[0]
    assert "client_max_body_size 1k;" in auth  # 論文などの本文を authn に渡さない
    # authn は Host を設定のオリジン（例：localhost:8080）と比べるので、ポート番号を含む $http_host を渡す
    assert "proxy_set_header Host $host;" not in server
    assert server.count("proxy_set_header Host $http_host;") == 2


def test_volume_owners_do_not_depend_on_start_order() -> None:
    """audit_log を core と gateway のどちらが先にマウントしても、gateway が書き込める所有者で作られる。"""
    dockerfile = (ROOT / "docker" / "python" / "Dockerfile").read_text(encoding="utf-8")
    core = dockerfile[
        dockerfile.index("FROM runtime AS core") : dockerfile.index("FROM runtime AS gateway")
    ]
    gateway = dockerfile[dockerfile.index("FROM runtime AS gateway") :]
    assert "chown -R app:app /data/private /data/lancedb /data/db /data/audit /keys/authn" in core
    gateway = gateway[: gateway.index("FROM runtime AS searcher")]
    assert "chown app:app /data/audit" in gateway
    authn = dockerfile[dockerfile.index("FROM runtime AS authn") :]
    authn = authn[: authn.index("FROM runtime AS dev")]
    assert "chown app:app /data/authn /keys" in authn and "chmod 700 /data/authn" in authn
    assert "COPY core/" not in authn and "/data/private" not in authn


def test_live_override_keeps_isolation() -> None:
    data = load("compose.live.yaml")
    services = data["services"]
    # 画面の作り直しはネットワークなしで行い、論文・データベースのボリュームをマウントしない
    watch = services["frontend_watch"]
    assert watch["network_mode"] == "none"
    assert watch["read_only"] is True
    assert not _mounted_volume_names(watch) & PRIVATE_VOLUMES
    # web と共有するのは画面の成果物だけ（web からは読み取り専用）
    (web_volume,) = services["web"]["volumes"]
    assert web_volume["source"] == "web_dist"
    assert web_volume["read_only"] is True
    # searcher に論文・データベースのボリュームを加えない
    assert not _mounted_volume_names(services["searcher"]) & PRIVATE_VOLUMES
