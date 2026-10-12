"""SQLite への保存（文書・解析・指摘・APIの利用回数）。"""

from pathlib import Path

import pytest

from core.analyze.models import Finding, ModelInfo
from core.analyze.pipeline import AnalysisSettings
from core.ingest.models import BBox
from core.llm.usage import UsageRow
from core.store import FindingNotFoundError, Store
from tests.unit.doc_factory import make_doc


def finding(fid: str, **kw: object) -> Finding:
    base: dict[str, object] = dict(
        finding_id=fid,
        doc_id="doc-test",
        category="logic",
        subcategory="overclaim",
        severity="major",
        page=1,
        bboxes=[BBox(page=1, x0=1, y0=2, x1=3, y1=4)],
        sentence_ids=["S-00001"],
        quote="q",
        issue="i",
        rationale="r",
        confidence="low",
        votes=2,
        samples=3,
        models=ModelInfo(primary="m"),
    )
    base.update(kw)
    return Finding.model_validate(base)


def test_store_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "db" / "mongai.sqlite3")
    doc = make_doc([("1 はじめに", "introduction", [["文です．"]])])
    store.save_document(doc)
    assert store.get_document("doc-test") == doc

    aid = store.create_analysis(doc.doc_id, kind="logic", settings=AnalysisSettings())
    store.save_findings(
        aid, [finding("F-0001"), finding("F-0002", category="readability", severity="suggestion")]
    )
    assert [f.finding_id for f in store.get_findings(aid)] == ["F-0001", "F-0002"]
    assert [f.finding_id for f in store.get_findings(aid, category="readability")] == ["F-0002"]

    store.set_finding_status(aid, "F-0001", "dismissed", "意図した表現のため")
    store.set_finding_status(aid, "F-0002", "accepted", "理由は保存されない")
    f1, f2 = store.get_findings(aid)
    assert (f1.status, f1.dismiss_reason) == ("dismissed", "意図した表現のため")
    assert (f2.status, f2.dismiss_reason) == ("accepted", None)

    # 改善案を付けて保存し直しても、利用者が変えたステータスは保たれる
    store.save_findings(aid, [finding("F-0001", suggestions=["案"])])
    f1 = store.get_findings(aid)[0]
    assert f1.suggestions == ["案"] and f1.status == "dismissed"
    assert store.get_findings(aid, status="dismissed")[0].finding_id == "F-0001"

    with pytest.raises(FindingNotFoundError):
        store.set_finding_status(aid, "F-9999", "accepted")

    usage = [
        UsageRow(
            endpoint="chat/completions",
            model="m",
            role="logic_check",
            calls=3,
            requests=4,
            retries=1,
            failures=0,
            prompt_tokens=10,
            completion_tokens=20,
            latency_s=1.5,
        )
    ]
    store.finish_analysis(aid, status="completed", warnings=["警告"], api_usage=usage)
    record = store.get_analysis(aid)
    assert record is not None
    assert (record.status, record.warnings, record.settings["output_lang"]) == (
        "completed",
        ["警告"],
        "paper",
    )
    assert record.finished_at is not None
    assert store.get_api_usage(aid) == usage
