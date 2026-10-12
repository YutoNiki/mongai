"""解析ジョブ（構造化 → 解析 → 保存）。LLM は模擬し、GROBID は使わない。"""

from pathlib import Path

import httpx
import pytest
import respx

from core.analyze.pipeline import AnalysisSettings
from core.jobs import run_analysis
from core.llm.client import OitomaClient
from core.store import Store
from core.store.files import save_paper
from tests.unit.test_analyze_pipeline import CHAT_URL, FakeLLM, config

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ja_extraphrase_jnlp2023.pdf"


async def test_run_analysis_saves_progress_findings_and_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("GROBID_URL", "http://127.0.0.1:9")  # 接続できない → GROBID なしで構造化
    store = Store(tmp_path / "db.sqlite3")
    doc_id = save_paper(FIXTURE.read_bytes())
    store.save_paper(doc_id, filename="paper.pdf", size=1)
    aid = store.create_analysis(
        doc_id, kind="logic", settings=AnalysisSettings(max_paragraphs=3), status="queued"
    )
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=FakeLLM())
        respx.post("http://127.0.0.1:9/api/processFulltextDocument").mock(
            return_value=httpx.Response(503)
        )
        async with OitomaClient(config(), api_key="k") as client:
            status = await run_analysis({}, aid, store=store, client=client)
    assert status == "completed"
    record = store.get_analysis(aid)
    assert record is not None and record.status == "completed"
    assert record.progress.stage == "suggestion"
    assert store.get_document(doc_id) is not None
    assert store.get_structures(aid)
    assert sum(r.requests for r in store.get_api_usage(aid)) > 0


async def test_run_analysis_failure_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    store = Store(tmp_path / "db.sqlite3")
    aid = store.create_analysis(
        "0" * 16, kind="logic", settings=AnalysisSettings(), status="queued"
    )
    async with OitomaClient(config(), api_key="k") as client:
        assert await run_analysis({}, aid, store=store, client=client) == "failed"
    record = store.get_analysis(aid)
    assert record is not None and record.status == "failed" and record.error
