"""注釈付きPDFのダウンロード（GET /api/analyses/{id}/annotated.pdf）。"""

from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from core.analyze.pipeline import AnalysisSettings
from core.api.main import app, get_store
from core.ingest.models import BBox
from core.store import Store
from core.store.files import paper_path, save_paper
from tests.unit.doc_factory import make_doc
from tests.unit.pdf_factory import Text, make_pdf
from tests.unit.test_annotate import finding


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Store, str]:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    store = Store(tmp_path / "db.sqlite3")
    app.dependency_overrides[get_store] = lambda: store
    pdf = make_pdf(
        tmp_path / "p.pdf", [[Text(72, 120, "The method clearly outperforms all baselines.")]]
    )
    doc_id = save_paper(pdf.read_bytes())
    return TestClient(app), store, doc_id


def make_analysis(store: Store, doc_id: str) -> str:
    doc = make_doc([("1 はじめに", "introduction", [["文．"]])]).model_copy(
        update={"doc_id": doc_id}
    )
    store.save_document(doc)
    aid = store.create_analysis(doc_id, kind="logic", settings=AnalysisSettings())
    box = BBox(page=1, x0=72, y0=110, x1=330, y1=123)
    store.save_findings(
        aid,
        [
            finding("F-0001", "logic", box, "clearly outperforms", doc_id=doc_id),
            finding("F-0002", "readability", box, "all baselines", doc_id=doc_id),
        ],
    )
    return aid


def test_download_annotated_pdf(env: tuple[TestClient, Store, str]) -> None:
    client, store, doc_id = env
    aid = make_analysis(store, doc_id)
    store.set_finding_status(aid, "F-0002", "dismissed", "意図どおり")
    response = client.get(f"/api/analyses/{aid}/annotated.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert f'filename="{doc_id}_annotated.pdf"' in response.headers["content-disposition"]
    with pymupdf.open(stream=response.content, filetype="pdf") as doc:
        titles = [a.info["title"] for a in doc[0].annots()]
    assert titles == ["MongAI F-0001"]  # 却下した指摘は書き込まない
    # 原本は変更しない
    assert paper_path(doc_id).read_bytes() != response.content


def test_not_found(env: tuple[TestClient, Store, str], tmp_path: Path) -> None:
    client, store, doc_id = env
    assert client.get("/api/analyses/A-unknown/annotated.pdf").status_code == 404
    aid = make_analysis(store, doc_id)
    paper_path(doc_id).unlink()
    assert client.get(f"/api/analyses/{aid}/annotated.pdf").status_code == 404


def test_paper_path_rejects_traversal() -> None:
    with pytest.raises(ValueError):
        paper_path("../../etc/passwd")
