"""ARQ のタイムアウト・タスク停止で、どの処理も実行中に取り残さない。"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

import core.jobs as jobs
from core.analyze.pipeline import AnalysisSettings
from core.instructions.store import InstructionStore
from core.lab.store import LabStore
from core.llm.client import OitomaClient
from core.rules.store import RuleStore
from core.store import Store
from tests.unit.auth_factory import make_user
from tests.unit.test_analyze_pipeline import config
from tests.unit.test_jobs import FakeGateway


@pytest.mark.parametrize("kind", ["analysis", "instructions", "rules", "lab"])
@pytest.mark.parametrize("timeout", [False, True])
async def test_cancelled_job_finishes_and_reraises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, timeout: bool
) -> None:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    db = Store(tmp_path / "db.sqlite3")
    owner = make_user(db.path)
    rules = RuleStore(db.path)
    lab = LabStore(db.path)
    instructions = InstructionStore(db.path)
    client = OitomaClient(config(), api_key="synthetic")
    monkeypatch.setattr(jobs, "OitomaClient", lambda: client)
    entered = asyncio.Event()
    gateway = FakeGateway()

    async def blocked(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await asyncio.Event().wait()

    if kind == "analysis":
        record_id = db.create_analysis(
            "0" * 16, owner_user_id=owner, kind="logic", settings=AnalysisSettings()
        )
        monkeypatch.setattr(jobs, "ingest_pdf", blocked)
        job = jobs.run_analysis({}, record_id, store=db, gateway=gateway)
    elif kind == "instructions":
        record_id = instructions.create_draft(owner, "course_report", "2000字以内", []).draft_id
        monkeypatch.setattr(jobs, "extract_requirements", blocked)
        job = jobs.run_instruction_extraction({}, record_id, store=instructions)
    elif kind == "rules":
        record_id = rules.create_document(owner, "synthetic.txt", "text").document_id
        monkeypatch.setattr(jobs, "extract_rules", blocked)
        job = jobs.run_rule_extraction({}, record_id, rule_store=rules)
    else:
        record_id = lab.create_document(owner, "synthetic.txt", "text").document_id
        monkeypatch.setattr(jobs, "extract_lab_knowledge", blocked)
        job = jobs.run_lab_extraction({}, record_id, lab_store=lab)
    task = asyncio.create_task(job)
    await asyncio.wait_for(entered.wait(), timeout=2)
    if timeout:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task, timeout=0.01)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert task.cancelled()
    if kind == "analysis":
        record = db.get_analysis(record_id)
        assert record is not None and record.finished_at is not None
        assert gateway.forgotten == ["0" * 16]
    elif kind == "instructions":
        record = instructions.get_draft(owner, record_id)
        assert instructions.claim_extraction(record_id) is None
    else:
        record = (
            rules.get_document(owner, record_id)
            if kind == "rules"
            else lab.get_document(owner, record_id)
        )
        assert record is not None and record.finished_at is not None
    assert record is not None and record.status == "failed" and "中断" in (record.error or "")
    assert client._http.is_closed
