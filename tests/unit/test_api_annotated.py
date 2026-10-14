"""注釈付きPDFのダウンロード（GET /api/analyses/{id}/annotated.pdf）。"""

from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from core.analyze.pipeline import AnalysisSettings
from core.api.main import app, get_principal, get_store
from core.auth.principal import AuthenticatedUser
from core.ingest.models import BBox
from core.store import Store
from core.store.files import new_doc_id, paper_path, save_paper
from tests.unit.auth_factory import make_user
from tests.unit.doc_factory import make_doc
from tests.unit.pdf_factory import Text, make_pdf
from tests.unit.test_annotate import finding


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Store, str]]:
    monkeypatch.setenv("MONGAI_DATA_DIR", str(tmp_path / "private"))
    store = Store(tmp_path / "db.sqlite3")
    owner = make_user(store.path)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_principal] = lambda: AuthenticatedUser(owner)
    pdf = make_pdf(
        tmp_path / "p.pdf", [[Text(72, 120, "The method clearly outperforms all baselines.")]]
    )
    doc_id = save_paper(pdf.read_bytes(), new_doc_id())
    store.save_paper(doc_id, owner_user_id=owner, filename="p.pdf", size=1)
    yield TestClient(app), store, doc_id
    app.dependency_overrides.clear()


def make_analysis(store: Store, doc_id: str) -> str:
    doc = make_doc([("1 はじめに", "introduction", [["文．"]])]).model_copy(
        update={"doc_id": doc_id}
    )
    store.save_document(doc)
    paper = store.get_paper(doc_id)
    assert paper is not None and paper.owner_user_id is not None
    aid = store.create_analysis(
        doc_id, owner_user_id=paper.owner_user_id, kind="logic", settings=AnalysisSettings()
    )
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
    assert titles == ["SI-NSA F-0001"]  # 却下した指摘は書き込まない
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


def test_annotated_pdf_hides_given_findings(env: tuple[TestClient, Store, str]) -> None:
    client, store, doc_id = env
    aid = make_analysis(store, doc_id)
    response = client.get(f"/api/analyses/{aid}/annotated.pdf?hide=F-0001&hide=F-9999")
    assert response.status_code == 200
    with pymupdf.open(stream=response.content, filetype="pdf") as doc:
        titles = [a.info["title"] for a in doc[0].annots()]
    # 指定した指摘だけ書き込まない（保存したステータスは変えない）
    assert titles == ["SI-NSA F-0002"]
    assert {f.finding_id: f.status for f in store.get_findings(aid)} == {
        "F-0001": "open",
        "F-0002": "open",
    }
