"""要件APIの実際の認可・版管理・出力・解析開始を確かめる。"""

from fastapi.testclient import TestClient

from core.api.main import app, get_principal
from core.auth.principal import AuthenticatedUser, Guest
from core.instructions.models import Requirement, RequirementResult
from core.instructions.store import InstructionStore
from core.store import Store
from tests.unit.auth_factory import make_user
from tests.unit.test_api import FakeQueue, current_user, upload
from tests.unit.test_api import ctx as ctx

ApiContext = tuple[TestClient, Store, FakeQueue, bytes]


def test_draft_queue_confirm_save_and_analyze(ctx: ApiContext) -> None:
    client, db, queue, pdf = ctx
    response = client.post(
        "/api/instruction-drafts",
        json={
            "document_type": "course_report",
            "raw_text": "2000字以内",
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    draft_id = created["draft_id"]
    assert queue.enqueued == [draft_id]
    assert (
        client.post(f"/api/instruction-drafts/{draft_id}/confirm", json={"revision": 1}).status_code
        == 409
    )
    store = InstructionStore(db.path)
    assert store.claim_extraction(draft_id) is not None
    draft = store.finish_extraction(
        current_user(),
        draft_id,
        [Requirement(text="2000字以内", kind="character_count", maximum=2000)],
    )
    response = client.post(
        f"/api/instruction-drafts/{draft_id}/confirm", json={"revision": draft.revision}
    )
    assert response.status_code == 200, response.text
    confirmed = response.json()
    response = client.post(
        "/api/instruction-profiles",
        json={
            "name": "講義A",
            "scope": "course",
            "draft_id": draft_id,
            "draft_revision": confirmed["revision"],
        },
    )
    assert response.status_code == 201, response.text
    profile = response.json()
    assert client.get("/api/instruction-profiles").json()[0]["name"] == "講義A"
    doc_id = upload(client, pdf)
    request = {
        "doc_id": doc_id,
        "document_type": "course_report",
        "instruction_draft_id": draft_id,
        "instruction_revision": confirmed["revision"],
    }
    response = client.post("/api/analyses", json=request)
    assert response.status_code == 201, response.text
    aid = response.json()["analysis"]["analysis_id"]
    assert queue.enqueued[-1] == aid
    snapshot = response.json()["analysis"]["settings"]["instruction_snapshot"]
    assert snapshot["requirements"][0]["maximum"] == 2000
    pending = client.get(f"/api/analyses/{aid}/requirements").json()
    assert pending["results"][0]["state"] == "pending"
    store.save_results(
        aid,
        [
            RequirementResult(
                requirement_id=draft.requirements[0].requirement_id,
                verdict="not_met",
                reason="2500字",
                coverage="full",
            )
        ],
    )
    db.finish_analysis(aid, status="completed")
    assert (
        client.get(f"/api/analyses/{aid}/requirements").json()["results"][0]["verdict"] == "not_met"
    )
    assert "満たさない" in client.get(f"/api/analyses/{aid}/requirements.csv").text
    assert (
        client.get(f"/api/analyses/{aid}/requirements.json").json()["snapshot"]["document_type"]
        == "course_report"
    )
    assert "2000字以内" in client.get(f"/api/analyses/{aid}/report.txt").text
    assert client.delete(f"/api/instruction-profiles/{profile['profile_id']}").status_code == 204
    assert client.get(f"/api/analyses/{aid}/requirements").json()["snapshot"] == pending["snapshot"]


def test_profile_and_draft_cannot_be_used_by_another_owner(ctx: ApiContext) -> None:
    client, db, _, pdf = ctx
    response = client.post("/api/instruction-drafts", json={"document_type": "course_report"})
    draft = response.json()
    edited = client.patch(
        f"/api/instruction-drafts/{draft['draft_id']}",
        json={
            "revision": 1,
            "requirements": [Requirement(text="具体例を含める").model_dump()],
        },
    ).json()
    confirmed = client.post(
        f"/api/instruction-drafts/{draft['draft_id']}/confirm",
        json={"revision": edited["revision"]},
    ).json()
    profile = client.post(
        "/api/instruction-profiles",
        json={
            "name": "講義A",
            "scope": "course",
            "draft_id": draft["draft_id"],
            "draft_revision": confirmed["revision"],
        },
    ).json()
    doc_id = upload(client, pdf)
    aid = client.post("/api/analyses", json={"doc_id": doc_id}).json()["analysis"]["analysis_id"]
    other = make_user(db.path, "other")
    app.dependency_overrides[get_principal] = lambda: AuthenticatedUser(other)
    assert client.get("/api/instruction-profiles").json() == []
    assert client.get(f"/api/instruction-drafts/{draft['draft_id']}").status_code == 404
    assert (
        client.patch(
            f"/api/instruction-drafts/{draft['draft_id']}",
            json={"revision": confirmed["revision"], "requirements": []},
        ).status_code
        == 404
    )
    assert client.delete(f"/api/instruction-profiles/{profile['profile_id']}").status_code == 404
    assert (
        client.post(
            "/api/instruction-drafts",
            json={"profiles": [{"profile_id": profile["profile_id"], "revision": 1}]},
        ).status_code
        == 404
    )
    for suffix in ("requirements", "requirements.csv", "requirements.json"):
        assert client.get(f"/api/analyses/{aid}/{suffix}").status_code == 404
    app.dependency_overrides[get_principal] = lambda: Guest()
    assert client.get("/api/instruction-profiles").status_code == 401
    assert client.post("/api/instruction-drafts", json={}).status_code == 401


def test_edited_draft_requires_new_confirmation(ctx: ApiContext) -> None:
    client, _, _, pdf = ctx
    draft = client.post("/api/instruction-drafts", json={"document_type": "course_report"}).json()
    path = f"/api/instruction-drafts/{draft['draft_id']}"
    confirmed = client.post(f"{path}/confirm", json={"revision": 1}).json()
    edited = client.patch(
        path,
        json={
            "revision": confirmed["revision"],
            "requirements": [Requirement(text="具体例を含める").model_dump()],
        },
    ).json()
    assert not edited["confirmed"]
    doc_id = upload(client, pdf)
    request = {
        "doc_id": doc_id,
        "document_type": "course_report",
        "instruction_draft_id": draft["draft_id"],
        "instruction_revision": confirmed["revision"],
    }
    assert client.post("/api/analyses", json=request).status_code == 409
    request["instruction_revision"] = edited["revision"]
    assert client.post("/api/analyses", json=request).status_code == 409
    latest = client.post(f"{path}/confirm", json={"revision": edited["revision"]}).json()
    request["instruction_revision"] = latest["revision"]
    assert client.post("/api/analyses", json=request).status_code == 201


def test_existing_rules_are_visible_only_in_paper_preview(ctx: ApiContext) -> None:
    client, *_ = ctx
    paper = client.post("/api/instruction-drafts", json={"document_type": "paper"}).json()
    assert paper["legacy_rules"]
    report = client.post("/api/instruction-drafts", json={"document_type": "lab_report"}).json()
    assert report["legacy_rules"] == []
    path = f"/api/instruction-drafts/{report['draft_id']}"
    assert (
        client.patch(
            path, json={"revision": 1, "requirements": [], "excluded_rule_ids": ["R-somebody-else"]}
        ).status_code
        == 409
    )


def test_input_bounds_and_csrf(ctx: ApiContext) -> None:
    client, *_ = ctx
    assert (
        client.post("/api/instruction-drafts", json={"raw_text": "あ" * 10001}).status_code == 422
    )
    assert (
        client.post("/api/instruction-drafts", json={"document_type": "unknown"}).status_code == 422
    )
    assert (
        client.post(
            "/api/instruction-drafts", json={}, headers={"Origin": "https://elsewhere.example"}
        ).status_code
        == 403
    )
