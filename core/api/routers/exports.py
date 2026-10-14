"""ダウンロード（指摘の CSV・JSON、テキストの要約、注釈付きPDF）。"""

import csv
import io
import json
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from core.annotate.labels import category_label, severity_label, subcategory_label
from core.annotate.pdf import annotate_pdf
from core.api.dependencies import StoreDep
from core.api.queries import (
    Category,
    Confidence,
    Severity,
    Status,
    analysis_or_404,
    filtered_findings,
    previous_and_diff,
)
from core.api.summary import count_findings
from core.report.text import text_report
from core.store.files import load_paper

router = APIRouter()


@router.get("/analyses/{analysis_id}/findings.csv")
def findings_csv(
    analysis_id: str,
    store: StoreDep,
    category: Category | None = None,
    severity: Severity | None = None,
    section_id: str | None = None,
    confidence: Confidence | None = None,
    status: Status | None = None,
) -> Response:
    findings = filtered_findings(
        store, analysis_id, category, severity, section_id, confidence, status
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "番号",
            "種類",
            "内容",
            "重大度",
            "確度",
            "ページ",
            "該当箇所",
            "指摘",
            "理由",
            "改善案",
            "ステータス",
            "却下の理由",
        ]
    )
    status_labels = {"open": "未対応", "accepted": "対応済み", "dismissed": "却下"}
    for f in findings:
        writer.writerow(
            [
                f.finding_id,
                category_label(f.category),
                subcategory_label(f.subcategory),
                severity_label(f.severity),
                ("高" if f.confidence == "high" else "低") if f.voted else "",
                f.page,
                f.quote,
                f.issue,
                f.rationale,
                " / ".join(f.suggestions),
                status_labels[f.status],
                f.dismiss_reason or "",
            ]
        )
    # Excel で文字化けしないよう BOM を付ける
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{analysis_id}_findings.csv"'},
    )


@router.get("/analyses/{analysis_id}/findings.json")
def findings_json(analysis_id: str, store: StoreDep) -> Response:
    """指摘の一覧の JSON（SPEC 3.4 の形式。却下したものも含め、ステータスと却下の理由を付ける）。"""
    record = analysis_or_404(store, analysis_id)
    doc = store.get_document(record.doc_id)
    paper = store.get_paper(record.doc_id)
    body = {
        "analysis_id": analysis_id,
        "created_at": record.created_at,
        "paper": {
            "doc_id": record.doc_id,
            "title": doc.title if doc else None,
            "filename": paper.filename if paper else None,
        },
        "findings": [f.model_dump(mode="json") for f in store.get_findings(analysis_id)],
    }
    return Response(
        content=json.dumps(body, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{analysis_id}_findings.json"'},
    )


@router.get("/analyses/{analysis_id}/report.txt")
def report_text(analysis_id: str, store: StoreDep) -> Response:
    """主要な問題と件数をまとめたテキストの要約（SPEC 3.5・6.6）。"""
    record = analysis_or_404(store, analysis_id)
    doc = store.get_document(record.doc_id)
    paper = store.get_paper(record.doc_id)
    findings = store.get_findings(analysis_id)
    previous, diff = previous_and_diff(store, analysis_id)
    text = text_report(
        title=doc.title if doc else None,
        filename=paper.filename if paper else None,
        analysis_id=analysis_id,
        created_at=record.created_at,
        counts=count_findings(findings, doc),
        findings=findings,
        previous_total=previous.total if previous else None,
        diff=diff,
    )
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{analysis_id}_report.txt"'},
    )


@router.get("/analyses/{analysis_id}/annotated.pdf")
def annotated_pdf(
    analysis_id: str,
    store: StoreDep,
    include_accepted: Annotated[bool, Query(description="対応済みの指摘も書き込む")] = False,
) -> Response:
    """注釈付きPDF。未対応の指摘（と、指定すれば対応済みの指摘）を書き込む。却下した指摘は書き込まない。"""
    analysis = analysis_or_404(store, analysis_id)
    try:
        paper = load_paper(analysis.doc_id)
    except ValueError:
        paper = None
    if paper is None:
        raise HTTPException(status_code=404, detail="論文のファイルが見つかりません")
    statuses = frozenset({"open", "accepted"} if include_accepted else {"open"})
    pdf = annotate_pdf(paper, store.get_findings(analysis_id), statuses=statuses)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{analysis.doc_id}_annotated.pdf"'},
    )
