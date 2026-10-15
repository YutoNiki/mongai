"""キュー・ファイル保存が失敗しても、登録済みの処理を待機中に取り残さない。"""

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.api.dependencies import JOB_SUBMISSION_ERROR
from core.instructions.store import InstructionStore
from core.store import Store
from tests.unit.test_api import FakeQueue, current_user, upload
from tests.unit.test_api import ctx as ctx

ApiContext = tuple[TestClient, Store, FakeQueue, bytes]


async def unavailable(*args: Any) -> None:
    raise ConnectionError("synthetic Redis error with secret")


def assert_unavailable(response: Any) -> None:
    assert response.status_code == 503
    assert response.json() == {"detail": JOB_SUBMISSION_ERROR}
    assert response.headers["retry-after"] == "30"


@pytest.mark.parametrize("with_instructions", [False, True])
def test_analysis_enqueue_failure_can_retry_and_delete(
    ctx: ApiContext, monkeypatch: pytest.MonkeyPatch, with_instructions: bool
) -> None:
    client, store, queue, pdf = ctx
    doc_id = upload(client, pdf)
    body: dict[str, Any] = {"doc_id": doc_id}
    if with_instructions:
        draft = client.post("/api/instruction-drafts", json={}).json()
        confirmed = client.post(
            f"/api/instruction-drafts/{draft['draft_id']}/confirm", json={"revision": 1}
        ).json()
        body.update(
            instruction_draft_id=draft["draft_id"], instruction_revision=confirmed["revision"]
        )
    with monkeypatch.context() as patch:
        patch.setattr(queue, "enqueue", unavailable)
        assert_unavailable(client.post("/api/analyses", json=body))
    failed = store.list_analyses(current_user())[0]
    assert failed.status == "failed" and failed.finished_at is not None
    assert failed.error == JOB_SUBMISSION_ERROR
    # 同じPDF・確認済みの要件を使って再試行できる。
    retried = client.post("/api/analyses", json=body)
    assert retried.status_code == 201
    store.finish_analysis(retried.json()["analysis"]["analysis_id"], status="completed")
    assert client.delete(f"/api/documents/{doc_id}").status_code == 204


@pytest.mark.parametrize("kind", ["rule", "lab"])
@pytest.mark.parametrize("failure", ["queue", "file"])
def test_material_submission_failure_can_delete(
    ctx: ApiContext, monkeypatch: pytest.MonkeyPatch, kind: str, failure: str
) -> None:
    client, _, queue, _ = ctx
    route = "/api/rule-documents" if kind == "rule" else "/api/lab/documents"
    if failure == "queue":
        monkeypatch.setattr(
            queue, "enqueue_rules" if kind == "rule" else "enqueue_lab", unavailable
        )
    else:

        def cannot_save(*args: Any) -> None:
            raise OSError("synthetic disk error with secret")

        target = (
            "core.api.routers.rules.save_document_file"
            if kind == "rule"
            else "core.api.routers.lab.save_lab_file"
        )
        monkeypatch.setattr(target, cannot_save)
    assert_unavailable(
        client.post(route, files={"file": ("notes.txt", b"Synthetic notes.", "text/plain")})
    )
    (doc,) = client.get(route).json()
    assert doc["status"] == "failed" and doc["finished_at"] is not None
    assert doc["error"] == JOB_SUBMISSION_ERROR
    assert client.delete(f"{route}/{doc['document_id']}").status_code == 204


def test_instruction_enqueue_failure_does_not_remain_pending(
    ctx: ApiContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db, queue, _ = ctx
    monkeypatch.setattr(queue, "enqueue_instructions", unavailable)
    assert_unavailable(client.post("/api/instruction-drafts", json={"raw_text": "2000字以内"}))
    with sqlite3.connect(db.path) as conn:
        draft_id = conn.execute("SELECT draft_id FROM instruction_drafts").fetchone()[0]
    store = InstructionStore(db.path)
    draft = store.get_draft(current_user(), draft_id)
    assert draft.status == "failed" and draft.error == JOB_SUBMISSION_ERROR
    assert store.recover_interrupted() == []
    assert store.claim_extraction(draft_id) is None


def test_queue_ack_failure_preserves_already_running_work(
    ctx: ApiContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store, queue, pdf = ctx
    doc_id = upload(client, pdf)

    async def acknowledged_late(analysis_id: str) -> None:
        store.set_status(analysis_id, "running")
        raise ConnectionError("synthetic lost Redis acknowledgement")

    monkeypatch.setattr(queue, "enqueue", acknowledged_late)
    assert_unavailable(client.post("/api/analyses", json={"doc_id": doc_id}))
    record = store.list_analyses(current_user())[0]
    assert record.status == "running" and record.finished_at is None
