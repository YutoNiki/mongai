"""位置を特定できる要件違反を既存の指摘へ関連付ける。"""

from core.analyze.models import Evidence, Finding, ModelInfo
from core.ingest.models import Document
from core.instructions.models import RequirementResult, Snapshot


def link_findings(
    doc: Document,
    snapshot: Snapshot,
    results: list[RequirementResult],
    findings: list[Finding],
    model: str,
) -> list[RequirementResult]:
    sentences = {s.sentence_id: s for s in doc.sentences}
    paragraphs = {p.paragraph_id: p for p in doc.paragraphs}
    requirements = {r.requirement_id: r for r in snapshot.requirements}
    next_number = max(
        (int(f.finding_id[2:]) for f in findings if f.finding_id[2:].isdigit()), default=0
    )
    linked: list[RequirementResult] = []
    for result in results:
        req = requirements[result.requirement_id]
        sids = list(
            dict.fromkeys(
                [
                    *result.sentence_ids,
                    *(
                        sid
                        for pid in result.paragraph_ids
                        if pid in paragraphs
                        for sid in paragraphs[pid].sentence_ids
                    ),
                ]
            )
        )
        sids = [sid for sid in sids if sid in sentences]
        boxes = [b for sid in sids for b in sentences[sid].bboxes]
        if result.verdict != "not_met" or not result.quote or not boxes:
            linked.append(result)
            continue
        evidence = Evidence(
            type="rule", ref=req.requirement_id, excerpt=f"{req.source}：{req.text}"
        )
        existing = next(
            (
                f
                for f in findings
                if f.category == "rule"
                and set(f.sentence_ids) & set(sids)
                and f.quote == result.quote
                and any(
                    e.type == "rule"
                    and (
                        e.ref == req.requirement_id
                        or e.excerpt == req.text
                        or (e.ref in requirements and requirements[e.ref].text == req.text)
                    )
                    for e in f.evidence
                )
            ),
            None,
        )
        if existing:
            if evidence not in existing.evidence:
                existing.evidence.append(evidence)
            linked.append(result.model_copy(update={"finding_id": existing.finding_id}))
            continue
        next_number += 1
        finding = Finding(
            finding_id=f"F-{next_number:04d}",
            doc_id=doc.doc_id,
            category="rule",
            subcategory="rule_violation",
            severity="major" if req.strength == "required" else "suggestion",
            section_id=sentences[sids[0]].section_id,
            page=boxes[0].page,
            bboxes=boxes,
            sentence_ids=sids,
            quote=result.quote,
            issue=f"提出要件「{req.text}」を満たしていません。",
            rationale=result.reason,
            evidence=[evidence],
            suggestions=[result.suggestion] if result.suggestion else [],
            confidence="low",
            votes=1,
            samples=1,
            models=ModelInfo(primary=model),
        )
        findings.append(finding)
        linked.append(result.model_copy(update={"finding_id": finding.finding_id}))
    return linked
