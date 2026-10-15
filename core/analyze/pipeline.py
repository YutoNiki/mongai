"""論理・読みやすさの解析：論証構造の抽出 →（段落内チェック・段落間チェック・引用の検証・ルールの判定を同時に）
→ 二重チェック → 改善案。"""

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
from core.analyze.second_opinion import ROLE as SECOND_ROLE
from core.analyze.second_opinion import apply_opinion, needs_second_opinion, second_opinion
from core.analyze.structure import extract_structure, fallback_structure
from core.analyze.suggest import suggest
from core.analyze.templates import text as template
from core.analyze.terms import lookup_terms
from core.analyze.voting import Voted, most_common
from core.gateway_client import GatewayClient
from core.ingest.models import BBox, Document, Lang, Sentence
from core.instructions.models import DocumentType, Snapshot
from core.instructions.review import document_for_review
from core.lab.check import LabMatch, candidates_for, check_lab, embed_targets
from core.lab.models import LabComment
from core.llm.budget import PromptTooLongError
from core.llm.client import OitomaClient, OitomaError
from core.llm.usage import UsageRow
from core.rules.check import (
    MAX_RULES_PER_CHECK,
    applicable_rules,
    check_rules,
    embed_missing_rules,
    group_targets,
    select_rules,
)
from core.rules.mechanical import RuleViolation, run_mechanical
from core.rules.models import Rule
from core.store import TermLookupRecord

logger = logging.getLogger(__name__)

# 進捗の通知：(工程, 完了数, 全体数)
ProgressCallback = Callable[[str, int, int], Awaitable[None] | None]
# 確定した指摘の通知（改善案を付ける前に、段落の判定が終わるたびに呼ぶ）
FindingsCallback = Callable[[list[Finding]], Awaitable[None] | None]


class AnalysisSettings(BaseModel):
    document_type: DocumentType = "paper"
    instruction_snapshot: Snapshot | None = None
    output_lang: OutputLang = "paper"
    max_paragraphs: int | None = Field(default=None, ge=1)
    min_paragraph_chars: int = Field(default=40, ge=1)
    with_flow_check: bool = True
    with_citation_check: bool = True
    # 引用内容の支持判定の上限（引用×文献の組の数）
    max_support_checks: int = Field(default=60, ge=0)
    # 承認済みのルールで判定する（機械判定と LLM 判定。SPEC 3.2・3.3-3）
    with_rule_check: bool = True
    # 研究室の過去の指摘に類似する箇所を検出する（SPEC 3.3-3）
    with_lab_check: bool = True
    # 重大・要修正の論理・読みやすさの指摘を、第2のモデルでも判定する（SPEC 3.3-4）
    with_double_check: bool = True
    # 定義なしで使われている一般的な専門用語を gateway に照会する（standard モードでは承認制。M7）
    with_term_lookup: bool = True
    with_suggestions: bool = True


class LogicAnalysis(BaseModel):
    findings: list[Finding]
    structures: list[ParagraphStructure]
    warnings: list[str] = Field(default_factory=list)
    api_usage: list[UsageRow] = Field(default_factory=list)
    terms: list[TermLookupRecord] = Field(default_factory=list)  # 未知の用語の照会


async def _notify(callback: Callable[..., Awaitable[None] | None] | None, *args: object) -> None:
    if callback is None:
        return
    result = callback(*args)
    if inspect.isawaitable(result):
        await result


class _StageProgress:
    """同時に進む工程の進捗を、1つの表示にまとめる。

    工程の順（stages）で最初の未完了の工程を表示する。段落内チェックの途中で段落間チェックや
    引用の検証が先に進んでも、表示は段落内チェックのまま進み、終わったら次の工程に移る。
    """

    def __init__(self, callback: ProgressCallback | None, stages: list[str]) -> None:
        self._callback = callback
        self._stages = stages
        self._state: dict[str, tuple[int, int]] = {}
        self._shown: tuple[str, int, int] | None = None

    async def __call__(self, stage: str, done: int, total: int) -> None:
        self._state[stage] = (done, total)
        current = next(
            (
                (st, *self._state.get(st, (0, 0)))
                for st in self._stages
                if st not in self._state or self._state[st][0] < self._state[st][1]
            ),
            (stage, done, total),
        )
        if current != self._shown:
            self._shown = current
            await _notify(self._callback, *current)

    def finish(self, stage: str) -> None:
        """工程を完了扱いにする（行わなかった工程など）。"""
        done, total = self._state.get(stage, (1, 1))
        self._state[stage] = (max(done, total, 1), max(total, 1))


def _sort_key(f: Finding) -> tuple[int, int, float, float]:
    box = f.bboxes[0] if f.bboxes else None
    return (SEVERITY_RANK[f.severity], f.page, box.y0 if box else 0.0, box.x0 if box else 0.0)


def _confidence(votes: int, samples: int) -> Literal["high", "low"]:
    """複数回の判定がすべて一致したときだけ high（1回だけの判定は low）。

    重大・要修正の指摘は、このあと二重チェック（第2のモデルとの一致）で決め直す。
    """
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

    def from_paragraph(self, voted: Voted[ParagraphIssue], section_id: str | None) -> Finding:
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
            confidence=_confidence(voted.votes, voted.samples),
            votes=voted.votes,
            samples=voted.samples,
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

    def from_violation(self, v: RuleViolation) -> Finding | None:
        if not v.bboxes:
            return None
        section_id = next(
            (self.sentences[s].section_id for s in v.sentence_ids if s in self.sentences), None
        )
        source = v.rule.source
        where = f"{source.doc}" + (f" p.{source.page}" if source.page else "")
        return Finding(
            finding_id=self._next_id(),
            doc_id=self.doc.doc_id,
            category="rule",
            subcategory=v.subcategory,
            severity=v.severity,
            section_id=section_id,
            page=v.bboxes[0].page,
            bboxes=v.bboxes,
            sentence_ids=v.sentence_ids,
            quote=v.quote,
            issue=v.issue,
            rationale=v.rationale,
            evidence=[
                Evidence(type="rule", ref=v.rule.rule_id, excerpt=f"{v.rule.rule}（{where}）")
            ],
            suggestions=v.suggestions,
            # 機械判定は確実なので高、LLM の判定は1回だけなので低
            confidence="high" if v.model == "mechanical" else "low",
            votes=1,
            samples=1,
            models=ModelInfo(primary=v.model),
        )

    def from_lab(self, m: LabMatch, lang: Lang) -> Finding | None:
        if not m.bboxes:
            return None
        section_id = next(
            (self.sentences[s].section_id for s in m.sentence_ids if s in self.sentences), None
        )
        source = m.comment.source
        where = source.doc + (f" p.{source.page}" if source.page else "")
        return Finding(
            finding_id=self._next_id(),
            doc_id=self.doc.doc_id,
            category="lab",
            subcategory="similar_past_comment",
            severity=m.severity,
            section_id=section_id,
            page=m.bboxes[0].page,
            bboxes=m.bboxes,
            sentence_ids=m.sentence_ids,
            quote=m.quote,
            issue=m.issue,
            rationale=template("lab.rationale", lang, comment=m.comment.comment, source=where),
            evidence=[Evidence(type="lab", ref=where, excerpt=m.comment.comment)],
            confidence="low",
            votes=1,
            samples=1,
            models=ModelInfo(primary=m.model),
        )

    def from_flow(self, voted: Voted[FlowIssue]) -> Finding | None:
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
            confidence=_confidence(voted.votes, voted.samples),
            votes=voted.votes,
            samples=voted.samples,
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
    rules: list[Rule] | None = None,
    rule_vectors: dict[str, list[float]] | None = None,
    lab_comments: list[LabComment] | None = None,
    lab_vectors: dict[str, list[float]] | None = None,
) -> LogicAnalysis:
    """論理・読みやすさの解析。gateway と analysis_id を渡すと引用の検証も、rules（承認済みのルール）を
    渡すとルールの判定も行う。rule_vectors はルールの埋め込み（ルールが多いときの絞り込みに使う）。
    lab_comments（研究室の過去の指摘）と lab_vectors（その埋め込み）を渡すと、類似箇所の検出も行う。"""
    settings = settings or AnalysisSettings()
    doc = document_for_review(doc, settings.document_type)
    prompt_lang = doc.lang
    out_lang = output_language(prompt_lang, doc.lang, settings.output_lang)
    out_lang_code: Lang = doc.lang if settings.output_lang == "paper" else "ja"
    targets = select_targets(
        doc,
        min_chars=settings.min_paragraph_chars,
        max_paragraphs=settings.max_paragraphs,
        document_type=settings.document_type,
    )
    warnings: list[str] = []
    known_sentences = {s.sentence_id for s in doc.sentences}
    sentences = {s.sentence_id: s for s in doc.sentences}
    builder = _Builder(doc, client)
    findings: list[Finding] = []
    term_records: list[TermLookupRecord] = []

    # 1. 論証構造の抽出
    done = 0
    await _notify(on_progress, "structure", done, len(targets))

    async def structure_of(t: Target) -> ParagraphStructure:
        nonlocal done
        try:
            result = await extract_structure(
                client,
                t,
                prompt_lang=prompt_lang,
                output_language=out_lang,
                document_type=settings.document_type,
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

    # 2〜4. 段落内チェック・段落間チェック・引用の検証は互いに依存しないので、同時に進める
    # （1つの工程の終わり際に、遅い数本の応答を待つ間もほかの工程の要求を送れる）
    stages = ["paragraph_check", "flow_check", "citation", "rule_check", "lab_check"]
    progress = _StageProgress(on_progress, stages)
    done = 0
    await progress("paragraph_check", done, len(targets))

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
            voted, _ = await check_paragraph(
                client,
                t,
                by_paragraph.get(t.paragraph.paragraph_id),
                prompt_lang=prompt_lang,
                output_language=out_lang,
                known_sentences=known_sentences,
                context_text=context,
                next_text=next_text,
                document_type=settings.document_type,
            )
        except OitomaError as exc:
            warnings.append(f"段落 {t.paragraph.paragraph_id} のチェックに失敗しました：{exc}")
        else:
            new = [builder.from_paragraph(v, t.paragraph.section_id) for v in voted]
            findings.extend(new)
            if new:
                await _notify(on_findings, new)
        done += 1
        await progress("paragraph_check", done, len(targets))

    async def paragraph_checks() -> None:
        await asyncio.gather(*(check(t) for t in targets))

    async def flow_checks() -> None:
        if not settings.with_flow_check:
            progress.finish("flow_check")
            return
        await progress("flow_check", 0, 1)
        try:
            flow, _, flow_warnings = await check_flow(
                client,
                targets,
                by_paragraph,
                prompt_lang=prompt_lang,
                output_language=out_lang,
                on_chunk=lambda done, total: progress("flow_check", done, total),
                document_type=settings.document_type,
            )
            warnings.extend(flow_warnings)
            new = [f for f in (builder.from_flow(v) for v in flow) if f is not None]
            findings.extend(new)
            if new:
                await _notify(on_findings, new)
        except OitomaError as exc:
            warnings.append(f"段落間のチェックに失敗しました：{exc}")
        progress.finish("flow_check")

    # 引用の検証（gateway 経由。学外に出るのは引用文献の書誌情報だけ）
    async def citation_checks() -> None:
        if not settings.with_citation_check:
            progress.finish("citation")
            return
        if gateway is None or analysis_id is None:
            warnings.append("引用の検証は行いませんでした（検索ゲートウェイが設定されていません）")
            progress.finish("citation")
            return
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
            on_progress=progress,
        )
        warnings.extend(citation.warnings)
        new = [f for f in (builder.from_draft(d) for d in citation.drafts) if f is not None]
        findings.extend(new)
        if new:
            await _notify(on_findings, new)
        if settings.with_term_lookup and targets:
            # 原稿は check_citations で gateway に登録済み。用語の照会は承認キューに積まれる
            terms = await lookup_terms(
                client, gateway, doc, targets, analysis_id=analysis_id, lang=prompt_lang
            )
            warnings.extend(terms.warnings)
            term_records.extend(terms.records)
            if terms.pending:
                warnings.append(
                    f"未知の用語 {len(terms.pending)} 語（{'、'.join(terms.pending)}）の照会を承認キューに"
                    "積みました。「承認キュー」の画面で承認すると、その用語だけを学外に送信します"
                )
        progress.finish("citation")

    # ルールの判定（機械判定は LLM を使わずにすぐ終わる。LLM 判定は段落ごと）
    async def rule_checks() -> None:
        approved = [r for r in rules or [] if r.status == "approved"]
        if not settings.with_rule_check or not approved:
            progress.finish("rule_check")
            return
        violations = run_mechanical(doc, approved, out_lang_code)
        new = [f for f in (builder.from_violation(v) for v in violations) if f is not None]
        findings.extend(new)
        if new:
            await _notify(on_findings, new)
        cited = {c.sentence_id for c in doc.citations}
        # 節ごと（長い節は段落のまとまりごと）に判定する
        groups = group_targets(targets)
        checked = 0
        await progress("rule_check", checked, len(groups))
        candidates = [applicable_rules(approved, group, doc, cited) for group in groups]
        vectors = dict(rule_vectors or {})
        # 並列の節判定で同じルールを何度も埋め込まないよう、必要な分を先にまとめる。
        to_embed = {
            r.rule_id: r
            for chosen in candidates
            if len(chosen) > MAX_RULES_PER_CHECK
            for r in chosen
        }
        try:
            await embed_missing_rules(client, list(to_embed.values()), vectors)
        except OitomaError as exc:
            warnings.append(f"ルールの埋め込みの作成に失敗しました：{exc}")
            progress.finish("rule_check")
            return

        async def check_group(group: list[Target], chosen: list[Rule]) -> None:
            nonlocal checked
            try:
                chosen = await select_rules(client, chosen, group, vectors)
                found = await check_rules(
                    client, group, chosen, prompt_lang=prompt_lang, output_language=out_lang
                )
            except OitomaError as exc:
                warnings.append(f"{group[0].section_label} のルールの判定に失敗しました：{exc}")
            else:
                made = [f for f in (builder.from_violation(v) for v in found) if f is not None]
                findings.extend(made)
                if made:
                    await _notify(on_findings, made)
            checked += 1
            await progress("rule_check", checked, len(groups))

        await asyncio.gather(*(check_group(g, c) for g, c in zip(groups, candidates, strict=True)))
        progress.finish("rule_check")

    # 研究室の過去の指摘に類似する箇所（埋め込みで候補を絞り、高速モデルで確かめる）
    async def lab_checks() -> None:
        comments = [c for c in lab_comments or [] if c.comment_id in (lab_vectors or {})]
        if not settings.with_lab_check or not comments or not targets:
            progress.finish("lab_check")
            return
        try:
            vectors = await embed_targets(client, targets)
        except OitomaError as exc:
            warnings.append(f"専門知識との照合に失敗しました：{exc}")
            progress.finish("lab_check")
            return
        compared = 0
        await progress("lab_check", compared, len(targets))

        async def check_target(t: Target, vector: list[float]) -> None:
            nonlocal compared
            try:
                found = await check_lab(
                    client,
                    t,
                    candidates_for(vector, comments, lab_vectors or {}),
                    prompt_lang=prompt_lang,
                    output_language=out_lang,
                )
            except OitomaError as exc:
                warnings.append(
                    f"段落 {t.paragraph.paragraph_id} の専門知識との照合に失敗しました：{exc}"
                )
            else:
                made = [f for f in (builder.from_lab(m, out_lang_code) for m in found) if f]
                findings.extend(made)
                if made:
                    await _notify(on_findings, made)
            compared += 1
            await progress("lab_check", compared, len(targets))

        await asyncio.gather(*(check_target(t, v) for t, v in zip(targets, vectors, strict=True)))
        progress.finish("lab_check")

    # 1つが例外で止まったら、ほかも取り消す（解析の失敗として扱う）
    async with asyncio.TaskGroup() as group:
        group.create_task(paragraph_checks())
        group.create_task(flow_checks())
        group.create_task(citation_checks())
        group.create_task(rule_checks())
        group.create_task(lab_checks())

    # 4. 二重チェック（重大・要修正の論理・読みやすさの指摘を、第2のモデルで独立に判定する）
    if settings.with_double_check:
        to_check = [i for i, f in enumerate(findings) if needs_second_opinion(f)]
        secondary = client.config.role(SECOND_ROLE).model
        checked = 0
        await _notify(on_progress, "double_check", checked, len(to_check))

        async def check_twice(index: int) -> None:
            nonlocal checked
            finding = findings[index]
            try:
                opinion = await second_opinion(
                    client,
                    doc,
                    finding,
                    prompt_lang=prompt_lang,
                    output_language=out_lang,
                    document_type=settings.document_type,
                )
                findings[index] = apply_opinion(finding, opinion, secondary)
            except OitomaError as exc:
                warnings.append(f"{finding.finding_id} の二重チェックに失敗しました：{exc}")
            checked += 1
            await _notify(on_progress, "double_check", checked, len(to_check))

        await asyncio.gather(*(check_twice(i) for i in to_check))

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
                rewrites = await suggest(
                    client,
                    doc,
                    finding,
                    prompt_lang=prompt_lang,
                    document_type=settings.document_type,
                    requirements=settings.instruction_snapshot.requirements
                    if settings.instruction_snapshot
                    else None,
                )
                findings[index] = finding.model_copy(update={"suggestions": rewrites})
            except (OitomaError, PromptTooLongError) as exc:
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
        terms=term_records,
    )
