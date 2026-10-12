"""論理・読みやすさの解析：論証構造の抽出 → 段落内チェック → 段落間チェック → 改善案。"""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, Field

from core.analyze.citation import FindingDraft, check_citations
from core.analyze.context import (
    OutputLang,
    Target,
    numbered_paragraph,
    output_language,
    select_targets,
)
from core.analyze.flow_check import check_flow
from core.analyze.models import (
    CATEGORY_OF,
    SEVERITY_RANK,
    Evidence,
    Finding,
    FlowIssue,
    ModelInfo,
    ParagraphIssue,
    ParagraphStructure,
)
from core.analyze.paragraph_check import ROLE as CHECK_ROLE
from core.analyze.paragraph_check import check_paragraph
from core.analyze.structure import extract_structure, fallback_structure
from core.analyze.suggest import suggest
from core.analyze.voting import Voted, most_common
from core.gateway_client import GatewayClient
from core.ingest.models import BBox, Document, Lang, Sentence
from core.llm.client import OitomaClient, OitomaError
from core.llm.usage import UsageRow

logger = logging.getLogger(__name__)

# 進捗の通知：(工程, 完了数, 全体数)
ProgressCallback = Callable[[str, int, int], Awaitable[None] | None]
# 確定した指摘の通知（改善案を付ける前に、段落の判定が終わるたびに呼ぶ）
FindingsCallback = Callable[[list[Finding]], Awaitable[None] | None]


class AnalysisSettings(BaseModel):
    output_lang: OutputLang = "paper"
    max_paragraphs: int | None = Field(default=None, ge=1)
    min_paragraph_chars: int = Field(default=40, ge=1)
    with_flow_check: bool = True
    with_citation_check: bool = True
    # 引用内容の支持判定の上限（引用×文献の組の数）
    max_support_checks: int = Field(default=60, ge=0)
    with_suggestions: bool = True


class LogicAnalysis(BaseModel):
    findings: list[Finding]
    structures: list[ParagraphStructure]
    warnings: list[str] = Field(default_factory=list)
    api_usage: list[UsageRow] = Field(default_factory=list)


async def _notify(callback: Callable[..., Awaitable[None] | None] | None, *args: object) -> None:
    if callback is None:
        return
    result = callback(*args)
    if inspect.isawaitable(result):
        await result


def _sort_key(f: Finding) -> tuple[int, int, float, float]:
    box = f.bboxes[0] if f.bboxes else None
    return (SEVERITY_RANK[f.severity], f.page, box.y0 if box else 0.0, box.x0 if box else 0.0)


def _confidence(votes: int, samples: int) -> Literal["high", "low"]:
    """複数回の判定がすべて一致したときだけ high（1回だけの判定は low）。M9 で二重チェックに置き換える。"""
    return "high" if samples >= 2 and votes == samples else "low"


class _Builder:
    """多数決を通った指摘を Finding に変換し、通し番号を振る。"""

    def __init__(self, doc: Document, client: OitomaClient) -> None:
        self.doc = doc
        self.sentences: dict[str, Sentence] = {s.sentence_id: s for s in doc.sentences}
        self.paragraphs = {p.paragraph_id: p for p in doc.paragraphs}
        self.model = client.config.role(CHECK_ROLE).model
        self.count = 0

    def _next_id(self) -> str:
        self.count += 1
        return f"F-{self.count:04d}"

    def _boxes(self, sentence_ids: list[str]) -> list[BBox]:
        return [b for sid in sentence_ids for b in self.sentences[sid].bboxes]

    def _evidence(self, sentence_ids: list[str]) -> list[Evidence]:
        return [
            Evidence(type="manuscript", ref=sid, excerpt=self.sentences[sid].text[:300])
            for sid in sentence_ids
            if sid in self.sentences
        ]

    def from_paragraph(
        self, voted: Voted[ParagraphIssue], n: int, section_id: str | None
    ) -> Finding:
        issue = voted.item
        boxes = self._boxes(issue.sentence_ids)
        return Finding(
            finding_id=self._next_id(),
            doc_id=self.doc.doc_id,
            category=CATEGORY_OF[issue.type],
            subcategory=issue.type,
            severity=most_common([m.severity for m in voted.members]),
            section_id=section_id,
            page=boxes[0].page,
            bboxes=boxes,
            sentence_ids=issue.sentence_ids,
            quote=issue.quote,
            issue=issue.issue,
            rationale=issue.rationale,
            evidence=self._evidence(
                [sid for sid in issue.related_sentence_ids if sid not in issue.sentence_ids]
            ),
            confidence=_confidence(voted.votes, n),
            votes=voted.votes,
            samples=n,
            models=ModelInfo(primary=self.model),
        )

    def from_draft(self, draft: FindingDraft) -> Finding | None:
        if not draft.bboxes:
            return None
        section_id = None
        for sid in draft.sentence_ids:
            if sid in self.sentences:
                section_id = self.sentences[sid].section_id
                break
        return Finding(
            finding_id=self._next_id(),
            doc_id=self.doc.doc_id,
            category="citation",
            subcategory=draft.subcategory,
            severity=draft.severity,
            section_id=section_id,
            page=draft.bboxes[0].page,
            bboxes=draft.bboxes,
            sentence_ids=draft.sentence_ids,
            quote=draft.quote,
            issue=draft.issue,
            rationale=draft.rationale,
            evidence=draft.evidence,
            suggestions=draft.suggestions,
            confidence="low",
            votes=1,
            samples=1,
            models=ModelInfo(primary=draft.model),
        )

    def from_flow(self, voted: Voted[FlowIssue], n: int) -> Finding | None:
        issue = voted.item
        para = self.paragraphs.get(issue.paragraph_id)
        if para is None or not para.sentence_ids:
            return None
        first = para.sentence_ids[0]
        related: list[str] = []
        if issue.previous_paragraph_id in self.paragraphs:
            prev = self.paragraphs[issue.previous_paragraph_id]
            related = prev.sentence_ids[-1:]
        boxes = self._boxes([first])
        return Finding(
            finding_id=self._next_id(),
            doc_id=self.doc.doc_id,
            category=CATEGORY_OF[issue.type],
            subcategory=issue.type,
            severity=most_common([m.severity for m in voted.members]),
            section_id=para.section_id,
            page=boxes[0].page,
            bboxes=boxes,
            sentence_ids=[first],
            quote=self.sentences[first].text,
            issue=issue.issue,
            rationale=issue.rationale,
            evidence=self._evidence(related),
            confidence=_confidence(voted.votes, n),
            votes=voted.votes,
            samples=n,
            models=ModelInfo(primary=self.model),
        )


async def analyze_logic(
    doc: Document,
    client: OitomaClient,
    settings: AnalysisSettings | None = None,
    *,
    on_progress: ProgressCallback | None = None,
    on_findings: FindingsCallback | None = None,
    gateway: GatewayClient | None = None,
    analysis_id: str | None = None,
) -> LogicAnalysis:
    """論理・読みやすさの解析。gateway と analysis_id を渡すと引用の検証も行う。"""
    settings = settings or AnalysisSettings()
    prompt_lang = doc.lang
    out_lang = output_language(prompt_lang, doc.lang, settings.output_lang)
    out_lang_code: Lang = doc.lang if settings.output_lang == "paper" else "ja"
    targets = select_targets(
        doc, min_chars=settings.min_paragraph_chars, max_paragraphs=settings.max_paragraphs
    )
    warnings: list[str] = []
    known_sentences = {s.sentence_id for s in doc.sentences}
    sentences = {s.sentence_id: s for s in doc.sentences}
    builder = _Builder(doc, client)
    findings: list[Finding] = []

    # 1. 論証構造の抽出
    done = 0
    await _notify(on_progress, "structure", done, len(targets))

    async def structure_of(t: Target) -> ParagraphStructure:
        nonlocal done
        try:
            result = await extract_structure(
                client, t, prompt_lang=prompt_lang, output_language=out_lang
            )
        except OitomaError as exc:
            warnings.append(
                f"段落 {t.paragraph.paragraph_id} の論証構造を抽出できませんでした：{exc}"
            )
            result = fallback_structure(t)
        done += 1
        await _notify(on_progress, "structure", done, len(targets))
        return result

    structures = await asyncio.gather(*(structure_of(t) for t in targets))
    by_paragraph = {s.paragraph_id: s for s in structures}

    # 2. 段落内チェック（終わった段落から順に指摘を確定する）
    done = 0
    await _notify(on_progress, "paragraph_check", done, len(targets))

    async def check(t: Target) -> None:
        nonlocal done
        context = (
            numbered_paragraph(
                [sentences[sid] for sid in t.previous.sentence_ids if sid in sentences]
            )
            if t.previous
            else ""
        )
        next_text = (
            numbered_paragraph([sentences[sid] for sid in t.next.sentence_ids if sid in sentences])
            if t.next
            else ""
        )
        try:
            voted, n = await check_paragraph(
                client,
                t,
                by_paragraph.get(t.paragraph.paragraph_id),
                prompt_lang=prompt_lang,
                output_language=out_lang,
                known_sentences=known_sentences,
                context_text=context,
                next_text=next_text,
            )
        except OitomaError as exc:
            warnings.append(f"段落 {t.paragraph.paragraph_id} のチェックに失敗しました：{exc}")
        else:
            new = [builder.from_paragraph(v, n, t.paragraph.section_id) for v in voted]
            findings.extend(new)
            if new:
                await _notify(on_findings, new)
        done += 1
        await _notify(on_progress, "paragraph_check", done, len(targets))

    await asyncio.gather(*(check(t) for t in targets))

    # 3. 段落間チェック
    if settings.with_flow_check:
        await _notify(on_progress, "flow_check", 0, 1)
        try:
            flow, n, flow_warnings = await check_flow(
                client, targets, by_paragraph, prompt_lang=prompt_lang, output_language=out_lang
            )
            warnings.extend(flow_warnings)
            new = [f for f in (builder.from_flow(v, n) for v in flow) if f is not None]
            findings.extend(new)
            if new:
                await _notify(on_findings, new)
        except OitomaError as exc:
            warnings.append(f"段落間のチェックに失敗しました：{exc}")
        await _notify(on_progress, "flow_check", 1, 1)

    # 4. 引用の検証（gateway 経由。学外に出るのは引用文献の書誌情報だけ）
    if settings.with_citation_check:
        if gateway is None or analysis_id is None:
            warnings.append("引用の検証は行いませんでした（検索ゲートウェイが設定されていません）")
        else:
            citation = await check_citations(
                doc,
                list(structures),
                client,
                gateway,
                analysis_id,
                prompt_lang=prompt_lang,
                output_lang=out_lang_code,
                output_language=out_lang,
                max_support_checks=settings.max_support_checks,
                on_progress=on_progress,
            )
            warnings.extend(citation.warnings)
            new = [f for f in (builder.from_draft(d) for d in citation.drafts) if f is not None]
            findings.extend(new)
            if new:
                await _notify(on_findings, new)

    # 5. 改善案（書誌の不一致のように、正しい書誌を案として既に持つ指摘は除く）
    if settings.with_suggestions:
        to_suggest = [
            i
            for i, f in enumerate(findings)
            if not f.suggestions and f.subcategory not in ("bib_not_found", "bib_mismatch")
        ]
        done = 0
        await _notify(on_progress, "suggestion", done, len(to_suggest))

        async def add_suggestions(index: int) -> None:
            nonlocal done
            finding = findings[index]
            try:
                rewrites = await suggest(client, doc, finding, prompt_lang=prompt_lang)
                findings[index] = finding.model_copy(update={"suggestions": rewrites})
            except OitomaError as exc:
                warnings.append(f"{finding.finding_id} の改善案を作れませんでした：{exc}")
            done += 1
            await _notify(on_progress, "suggestion", done, len(to_suggest))

        await asyncio.gather(*(add_suggestions(i) for i in to_suggest))

    findings.sort(key=_sort_key)
    return LogicAnalysis(
        findings=findings,
        structures=list(structures),
        warnings=warnings,
        api_usage=client.usage.rows(),
    )
