"""compose.yaml の静的検査（SPEC 2.2・7.1）。

実際の通信の検証は tests/security/run.sh で行う。ここでは、設定ファイルの変更で
分離が崩れていないかを、コンテナを起動せずに確認する。
"""

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
}

EXPECTED_NETWORKS = {
    "web": {"ui_net", "internal_net"},
    "core": {"internal_net"},
    "worker": {"internal_net"},
    "gateway": {"internal_net", "gateway_net"},
    "searcher": {"gateway_net", "egress_net"},
    "redis": {"internal_net"},
    "grobid": {"internal_net"},
    "oitoma_proxy": {"internal_net", "oitoma_net"},
    "egress_proxy": {"egress_net", "outbound_net"},
}

PRIVATE_VOLUMES = {"data_private", "lancedb", "app_db", "audit_log"}


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
    for name in ("internal_net", "gateway_net", "egress_net"):
        assert networks[name].get("internal") is True, name
    assert set(networks) == {
        "ui_net",
        "internal_net",
        "oitoma_net",
        "gateway_net",
        "egress_net",
        "outbound_net",
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


@pytest.mark.parametrize("service", ["web", "gateway", "searcher", "redis", "grobid"])
def test_private_data_only_in_core(compose: dict[str, Any], service: str) -> None:
    allowed = {"gateway": {"audit_log"}}.get(service, set())
    mounted = _mounted_volume_names(compose["services"][service]) & PRIVATE_VOLUMES
    assert mounted <= allowed, (service, mounted)


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
