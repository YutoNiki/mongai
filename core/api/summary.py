"""ダッシュボード用の集計。"""

from collections import Counter

from core.analyze.context import SKIPPED_ROLES
from core.analyze.models import Finding
from core.api.schemas import Counts, SectionCount
from core.ingest.models import Document

SEVERITIES = ("critical", "major", "suggestion")
CATEGORIES = ("logic", "readability", "rule", "citation", "lab")


def _top_key(number: str | None, section_id: str) -> str:
    return number.split(".")[0] if number else section_id


def count_findings(findings: list[Finding], doc: Document | None) -> Counts:
    active = [f for f in findings if f.status != "dismissed"]
    by_severity: Counter[str] = Counter(f.severity for f in active)
    by_category: Counter[str] = Counter(f.category for f in active)

    sections: list[SectionCount] = []
    key_of: dict[str, str] = {}
    if doc is not None:
        # 小見出し（3.1 など）は大見出し（3）にまとめ、論文内の順に並べる
        labels: dict[str, str] = {}
        order: list[str] = []
        for s in doc.sections:
            key = _top_key(s.number, s.section_id)
            key_of[s.section_id] = key
            if key not in labels:
                if s.role in SKIPPED_ROLES:
                    continue
                labels[key] = f"{s.number} {s.title}" if s.number else s.title
                order.append(key)
        per_key = Counter(key_of.get(f.section_id or "", "") for f in active)
        sections = [SectionCount(key=k, label=labels[k], count=per_key.get(k, 0)) for k in order]

    return Counts(
        total=len(active),
        by_severity={s: by_severity.get(s, 0) for s in SEVERITIES},
        by_category={c: by_category.get(c, 0) for c in CATEGORIES},
        by_section=sections,
        section_of=key_of,
        by_status=dict(Counter(f.status for f in findings)),
        by_confidence=dict(Counter(f.confidence for f in active)),
    )
