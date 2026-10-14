"""API のルート表（ルーターに分けても URL・メソッド・ステータス・依存性が変わらないこと）。"""

import re
from typing import Any

from fastapi.routing import APIRoute, iter_route_contexts

from core.api import dependencies, main
from core.api.main import app

# (メソッド, パス, 成功時のステータス。None は既定の 200)
EXPECTED = {
    ("GET", "/api/healthz", None),
    ("POST", "/api/documents", 201),
    ("GET", "/api/documents/{doc_id}/paper.pdf", None),
    ("DELETE", "/api/documents/{doc_id}", 204),
    ("GET", "/api/usage/today", None),
    ("POST", "/api/analyses", 201),
    ("GET", "/api/analyses", None),
    ("GET", "/api/analyses/{analysis_id}", None),
    ("GET", "/api/analyses/{analysis_id}/findings", None),
    ("PATCH", "/api/analyses/{analysis_id}/findings/{finding_id}", None),
    ("GET", "/api/analyses/{analysis_id}/findings.csv", None),
    ("GET", "/api/analyses/{analysis_id}/terms", None),
    ("GET", "/api/analyses/{analysis_id}/diff", None),
    ("GET", "/api/analyses/{analysis_id}/findings.json", None),
    ("GET", "/api/analyses/{analysis_id}/report.txt", None),
    ("GET", "/api/analyses/{analysis_id}/annotated.pdf", None),
    ("GET", "/api/audit-log", None),
    ("GET", "/api/gateway/queue", None),
    ("POST", "/api/gateway/queue/{request_id}/{action}", None),
    ("GET", "/api/rules", None),
    ("PATCH", "/api/rules/{rule_id}", None),
    ("POST", "/api/rules/bulk-status", None),
    ("GET", "/api/rule-documents", None),
    ("POST", "/api/rule-documents", 201),
    ("DELETE", "/api/rule-documents/{document_id}", 204),
    ("GET", "/api/lab/documents", None),
    ("POST", "/api/lab/documents", 201),
    ("DELETE", "/api/lab/documents/{document_id}", 204),
    ("GET", "/api/lab/comments", None),
    ("DELETE", "/api/lab/comments/{comment_id}", 204),
}


def _routes() -> list[tuple[str, str, APIRoute]]:
    routes: list[tuple[str, str, APIRoute]] = []
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute):
            assert context.path is not None
            routes.extend((method, context.path, route) for method in route.methods or ())
    return routes


def test_route_table_is_unchanged() -> None:
    actual = [(method, path, route.status_code) for method, path, route in _routes()]
    assert len(actual) == len(set(actual))
    assert set(actual) == EXPECTED


def test_routes_do_not_depend_on_registration_order() -> None:
    """どの URL も、同じメソッドのルートのうち1つにしか当てはまらない（ルーターの登録順で解釈が変わらない）。"""
    routes = _routes()
    for method, path, _ in routes:
        concrete = re.sub(r"\{[^}]+\}", "X-1", path)
        matched = [
            p
            for m, p, _ in routes
            if m == method and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", p), concrete)
        ]
        assert matched == [path], (method, concrete, matched)


def test_dependencies_are_shared_with_main() -> None:
    """テストが core.api.main から取る関数と、ルーターが使う関数が同じ（dependency_overrides が効く）。"""
    names = (
        "get_store",
        "get_rule_store",
        "get_lab_store",
        "get_queue",
        "get_gateway",
        "get_principal",
        "get_verifier",
        "get_user_directory",
    )
    for name in names:
        assert getattr(main, name) is getattr(dependencies, name)
    used = {dep.call for _, _, route in _routes() for dep in route.dependant.dependencies}
    assert used == {
        dependencies.get_store,
        dependencies.get_rule_store,
        dependencies.get_lab_store,
        dependencies.get_queue,
        dependencies.get_gateway,
        dependencies.get_principal,
        dependencies.require_user,
    }


def _depends_on(dependant: Any, target: Any) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def test_every_route_checks_the_principal() -> None:
    """/api/healthz 以外のすべてのルートが主体（get_principal）を確かめる（付け忘れの検出）。"""
    missing = [
        f"{method} {path}"
        for method, path, route in _routes()
        if path != "/api/healthz" and not _depends_on(route.dependant, dependencies.get_principal)
    ]
    assert missing == []
