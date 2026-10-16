"""通常解析と提出要件判定の同時実行・保存・取り消し。合成データだけを使う。"""

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import core.jobs as jobs
from core.analyze.pipeline import AnalysisSettings, LogicAnalysis
from core.ingest.models import Document
from core.instructions.models import Requirement, RequirementResult, Snapshot
from core.instructions.store import InstructionStore
from core.llm.client import OitomaClient
from core.store import Store
from core.store.files import new_doc_id, save_paper
from tests.unit.auth_factory import make_user
from tests.unit.doc_factory import make_doc
from tests.unit.test_instructions import instruction_config
from tests.unit.test_jobs import FakeGateway


@pytest.mark.parametrize("outcome", ["completed", "cancelled", "failed"])
async def test_requirements_start_during_analysis_and_share_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    db = Store(tmp_path / "db.sqlite3")
    owner = make_user(db.path)
    doc_id = save_paper(b"synthetic PDF stub", new_doc_id())
    db.save_paper(doc_id, owner_user_id=owner, filename="synthetic.pdf", size=18)
    req = Requirement(text="合成の条件")
    aid = db.create_analysis(
        doc_id,
        owner_user_id=owner,
        kind="logic",
        settings=AnalysisSettings(
            instruction_snapshot=Snapshot(document_type="course_report", requirements=[req])
        ),
    )
    doc = make_doc([("1 本論", "other", [["合成の本文である。"]])]).model_copy(
        update={"doc_id": doc_id}
    )
    normal_started = asyncio.Event()
    requirement_started = asyncio.Event()
    release = asyncio.Event()
    normal_stopped = asyncio.Event()
    requirement_stopped = asyncio.Event()

    async def ingest(*args: Any, **kwargs: Any) -> Document:
        return doc

    async def analyze(*args: Any, **kwargs: Any) -> LogicAnalysis:
        normal_started.set()
        try:
            await release.wait()
            if outcome == "failed":
                raise RuntimeError("synthetic failure")
            return LogicAnalysis(findings=[], structures=[])
        finally:
            normal_stopped.set()

    async def requirements(*args: Any, **kwargs: Any) -> list[RequirementResult]:
        requirement_started.set()
        item = RequirementResult(
            requirement_id=req.requirement_id, verdict="unknown", reason="資料不足"
        )
        try:
            await kwargs["on_result"](item, 1, 1)
            if outcome != "completed":
                await asyncio.Event().wait()
            return [item]
        finally:
            requirement_stopped.set()

    monkeypatch.setattr(jobs, "ingest_pdf", ingest)
    monkeypatch.setattr(jobs, "analyze_logic", analyze)
    monkeypatch.setattr(jobs, "check_requirements", requirements)
    monkeypatch.setattr(jobs, "relocate_findings", lambda data, findings: (findings, Counter()))
    gateway = FakeGateway()
    async with OitomaClient(instruction_config(), api_key="synthetic") as client:
        task = asyncio.create_task(
            jobs.run_analysis({}, aid, store=db, client=client, gateway=gateway)
        )
        try:
            await asyncio.wait_for(
                asyncio.gather(normal_started.wait(), requirement_started.wait()), 2
            )
            # 要件の結果は通常解析が終わる前に保存されている。
            assert len(InstructionStore(db.path).results(aid)) == 1
            assert not normal_stopped.is_set()
            if outcome == "cancelled":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                assert await asyncio.wait_for(task, 2) == outcome
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
    assert normal_stopped.is_set() and requirement_stopped.is_set()
    record = db.get_analysis(aid)
    assert record is not None and record.status == (
        "completed" if outcome == "completed" else "failed"
    )
    assert gateway.forgotten == [doc_id]
