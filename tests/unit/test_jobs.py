"""解析ジョブ（構造化 → 解析 → 保存）。LLM は模擬し、GROBID は使わない。"""

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from core.analyze.pipeline import AnalysisSettings
from core.gateway_client import GatewayClient
from core.jobs import run_analysis
from core.llm.client import OitomaClient
from core.store import Store, today_jst
from core.store.files import new_doc_id, save_paper
from shared.schemas import GatewayResponse
from tests.unit.auth_factory import make_user
from tests.unit.test_analyze_pipeline import CHAT_URL, FakeLLM, config

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ja_extraphrase_jnlp2023.pdf"


class FakeGateway(GatewayClient):
    """外部要求をすべて止める gateway（offline モード相当）。"""

    def __init__(self) -> None:
        super().__init__("http://gateway.test")
        self.registered: list[str] = []
        self.forgotten: list[str] = []

    async def register(self, guard: dict[str, Any]) -> None:
        self.registered.append(str(guard["doc_id"]))

    async def forget(self, doc_id: str) -> None:
        self.forgotten.append(doc_id)

    async def submit(self, analysis_id: str, doc_id: str, request: Any) -> GatewayResponse:
        assert self.owner_id is not None  # 要求は解析の所有者の名前で出す
        return GatewayResponse(request_id="R-x", decision="blocked", reason="offline モード")


async def test_run_analysis_saves_progress_findings_and_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("GROBID_URL", "http://127.0.0.1:9")  # 接続できない → GROBID なしで構造化
    store = Store(tmp_path / "db.sqlite3")
    owner = make_user(store.path)
    doc_id = save_paper(FIXTURE.read_bytes(), new_doc_id())
    store.save_paper(doc_id, owner_user_id=owner, filename="paper.pdf", size=1)
    aid = store.create_analysis(
        doc_id,
        owner_user_id=owner,
        kind="logic",
        settings=AnalysisSettings(max_paragraphs=3),
        status="queued",
    )
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=FakeLLM())
        respx.post("http://127.0.0.1:9/api/processFulltextDocument").mock(
            return_value=httpx.Response(503)
        )
        async with OitomaClient(config(), api_key="k") as client:
            gateway = FakeGateway()
            status = await run_analysis({}, aid, store=store, client=client, gateway=gateway)
    assert status == "completed"
    assert gateway.registered == [doc_id] and gateway.forgotten == [doc_id]
    record = store.get_analysis(aid)
    assert record is not None
    assert any("offline" in w for w in record.warnings), record.warnings
    record = store.get_analysis(aid)
    assert record is not None and record.status == "completed"
    assert record.progress.stage == "locate"  # 最後に指摘の位置を確かめる
    assert store.get_document(doc_id) is not None
    assert store.get_structures(aid)
    usage = store.get_api_usage(aid)
    assert sum(r.requests for r in usage) > 0
    # 日ごとの集計（所有者の分）にも、同じ回数・トークン数が入る
    today = store.get_daily_usage(today_jst(), user_id=owner)
    assert today.requests == sum(r.requests for r in usage)
    assert today.completion_tokens == sum(r.completion_tokens for r in usage) > 0


async def test_run_analysis_failure_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    store = Store(tmp_path / "db.sqlite3")
    aid = store.create_analysis(
        "0" * 16,
        owner_user_id=make_user(store.path),
        kind="logic",
        settings=AnalysisSettings(),
        status="queued",
    )
    async with OitomaClient(config(), api_key="k") as client:
        assert (
            await run_analysis({}, aid, store=store, client=client, gateway=FakeGateway())
            == "failed"
        )
    record = store.get_analysis(aid)
    assert record is not None and record.status == "failed" and record.error


async def test_run_analysis_refuses_sample_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """共通デモ（所有者のいない公開データ）の解析は、ワーカーでも実行しない。"""
    from core.sample import SAMPLE_ANALYSIS_ID, install_sample

    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    store = Store(tmp_path / "db.sqlite3")
    install_sample(store)
    async with OitomaClient(config(), api_key="k") as client:
        status = await run_analysis(
            {}, SAMPLE_ANALYSIS_ID, store=store, client=client, gateway=FakeGateway()
        )
    assert status == "failed"
    record = store.get_analysis(SAMPLE_ANALYSIS_ID)
    assert record is not None and record.status == "completed"  # デモの記録は変えない
