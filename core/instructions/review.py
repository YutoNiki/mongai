"""文書種別の解釈は解析単位で行い、共有のPDF抽出結果は書き換えない。"""

import json
from typing import Any

from core.ingest.models import Document, Lang
from core.instructions.models import DOCUMENT_LABELS, DocumentType, Snapshot
from core.llm.prompts import load_prompt


def document_for_review(doc: Document, document_type: DocumentType) -> Document:
    if document_type == "paper":
        return doc
    return doc.model_copy(
        update={
            "sections": [
                s.model_copy(update={"role": "other"})
                if s.section_id == "SEC-00" and s.role in ("title", "abstract")
                else s
                for s in doc.sections
            ]
        }
    )


def review_prompt(name: str, lang: Lang, document_type: DocumentType, **values: str) -> str:
    system = load_prompt(name, lang, **values)
    if document_type != "paper":
        labels = {
            "paper": "research paper",
            "course_report": "course report",
            "lab_report": "lab report",
        }
        system += "\n\n" + load_prompt(
            "report_context",
            lang,
            document_type=DOCUMENT_LABELS[document_type] if lang == "ja" else labels[document_type],
        )
    return system


def comparison_key(settings: dict[str, Any]) -> str:
    raw = settings.get("instruction_snapshot")
    snapshot = Snapshot.model_validate(raw) if raw else None
    requirements = (
        [
            r.model_dump(exclude={"requirement_id", "source", "source_excerpt", "enabled"})
            for r in snapshot.requirements
            if r.enabled
        ]
        if snapshot
        else []
    )
    return json.dumps(
        {
            "document_type": settings.get("document_type", "paper"),
            "requirements": sorted(requirements, key=lambda r: json.dumps(r, sort_keys=True)),
            "excluded_rules": sorted(snapshot.excluded_rule_ids) if snapshot else [],
            "legacy_rules": sorted(
                [
                    r.model_dump(
                        exclude={"rule_id", "source", "merged_sources", "updated_at", "document_id"}
                    )
                    for r in snapshot.legacy_rules
                    if r.rule_id not in snapshot.excluded_rule_ids
                ],
                key=lambda r: json.dumps(r, sort_keys=True),
            )
            if snapshot
            else None,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
