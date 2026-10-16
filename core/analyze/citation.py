"""引用の検証（SPEC 3.3-3 の「引用」）。

1. 書誌の照合：参考文献ごとに gateway 経由で書誌データベースを引き、存在と一致
   （著者・年・タイトル・巻・ページ）を確かめる
2. 引用内容の支持：引用している文の主張を、引用先のアブストラクトが支持しているかを思考モデルで判定する

学外に出るのは、引用文献の書誌情報（タイトル・第一著者・年・DOI）だけ。原稿の文は送らない。
外部から取得したテキストは <external_data> で囲み、その中の指示には従わせない。
"""

import asyncio
import inspect
import re
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from pydantic import ValidationError

from core.analyze.evidence import quote_matches
from core.analyze.models import (
    ArgumentStructure,
    Evidence,
    ParagraphStructure,
    Severity,
    _LLMOutput,
)
from core.analyze.templates import text as template
from core.gateway_client import GatewayClient, GatewayError
from core.ingest.models import BBox, Document, Lang, Reference, Sentence
from core.llm.client import OitomaClient, OitomaError
from core.llm.external import external_data_notice, wrap_external
from core.llm.prompts import load_prompt
from shared.schemas import BibLookup, BibResult, OATextFetch

ROLE_SUPPORT = "citation_support"
# 書誌の候補を同じ文献とみなすタイトルの類似度
MATCH_THRESHOLD = 0.85
# タイトルの不一致として指摘する類似度（これ未満。同じ文献だが表記がずれている）
TITLE_MISMATCH = 0.95
# 同じ文献とみなした記録の DOI の種類の上限（これを超えると転載が多く、原本を特定できないとみなす）
MAX_DISTINCT_DOIS = 2
_KEEP = re.compile(r"[0-9a-z぀-ヿ㐀-䶿一-鿿]")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
_LABEL = re.compile(r"^\s*\[?\d{1,3}[\].]\s*")

ProgressCallback = Callable[[str, int, int], Awaitable[None] | None]


class SupportJudgement(_LLMOutput):
    verdict: Literal["supports", "partially_supports", "does_not_support", "unclear"]
    reason: str
    evidence: str | None = None


@dataclass
class FindingDraft:
    """番号を振る前の引用の指摘（pipeline で Finding にする）。"""

    subcategory: str
    severity: Severity
    sentence_ids: list[str]
    bboxes: list[BBox]
    quote: str
    issue: str
    rationale: str
    evidence: list[Evidence] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    model: str = "-"


@dataclass
class CitationResult:
    drafts: list[FindingDraft] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    return "".join(_KEEP.findall(unicodedata.normalize("NFKC", text).lower()))


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if len(nb) >= 15 and nb in na:  # 参考文献の原文（著者・年を含む）の中にタイトルが含まれる
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def is_cjk(text: str) -> bool:
    return _CJK.search(text) is not None


def lookup_title(ref: Reference) -> str | None:
    if ref.title:
        return ref.title[:300]
    if ref.raw:
        return " ".join(_LABEL.sub("", ref.raw).split())[:300]
    return None


def _author_matches(ref: Reference, record: BibResult) -> bool:
    """第一著者が合うか。GROBID が姓を取り違える（"Trevor Anthony Cohn" の姓を Anthony とする）ことがあるため、
    データベース側の姓が、参考文献の第一著者の表記か原文の書き出しに含まれていれば一致とみなす。"""
    if not ref.surnames or not record.authors:
        return True
    surname = normalize(ref.surnames[0])
    first = normalize(record.authors[0])
    if surname and (surname in first or first in surname):
        return True
    record_tokens = [
        t for t in record.authors[0].replace(",", " ").split() if len(t.strip(".")) >= 2
    ]
    record_surname = normalize(record_tokens[-1]) if record_tokens else ""
    head = normalize(" ".join([ref.authors[0] if ref.authors else "", (ref.raw or "")[:60]]))
    return bool(record_surname) and record_surname in head


_TITLE_BREAK = re.compile(r"[.:：]\s*")


def _split_head(title: str) -> tuple[str, bool]:
    """タイトルの最初の区切り（「. 」「: 」）より前の部分と、区切りがあったかを返す。"""
    parts = _TITLE_BREAK.split(title.strip(), maxsplit=1)
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        return title, False
    return parts[0].strip(), True


def matching_records(ref: Reference, candidates: list[BibResult]) -> list[BibResult]:
    """候補のうち、参考文献と同じ文献とみなせるもの（DOI が一致するか、タイトルが十分に似ていて著者か年が合う）。

    同じ文献でも、会議版・プレプリント・再掲など複数の記録があるため、すべてを返す（良く合う順）。
    """
    if ref.doi:
        by_doi = [c for c in candidates if c.doi and c.doi.lower() == ref.doi.lower()]
        if by_doi:
            return by_doi
    title = lookup_title(ref)
    if not title:
        return []
    # GROBID がタイトルに掲載誌を続けて読むことがある（"Random Forests. Machine Learning"）。また、
    # データベースが副題を除いたタイトルだけを持つことがある（"XGBoost: A Scalable ..." → "XGBoost"）。
    # その場合は最初の「. 」「: 」までも比べる。ただし短いタイトルで別の文献を拾わないよう、第一著者の一致を必須にする
    head, sep = _split_head(title)
    scored: list[tuple[bool, float, BibResult]] = []
    for c in candidates:
        if not c.title:
            continue
        sim = title_similarity(title, c.title)
        author_ok = _author_matches(ref, c)
        if sim < MATCH_THRESHOLD and sep and author_ok and ref.surnames:
            sim = title_similarity(head, c.title)
        if sim < MATCH_THRESHOLD:
            continue
        if not (author_ok or (ref.year and c.year == ref.year)):
            continue
        scored.append((bool(ref.year and c.year == ref.year), sim, c))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [c for _, _, c in scored]


def match_record(ref: Reference, candidates: list[BibResult]) -> BibResult | None:
    records = matching_records(ref, candidates)
    return records[0] if records else None


def _norm_pages(pages: str) -> str:
    return re.sub(r"[‐-―−–-]+", "-", pages.replace("pp.", "").replace("p.", "")).replace(" ", "")


def _first_page(pages: str) -> int | None:
    match = re.match(r"\d+", _norm_pages(pages))
    return int(match.group(0)) if match else None


def compare(
    ref: Reference, records: list[BibResult], *, by_doi: bool = False
) -> list[tuple[str, str, str, Severity]]:
    """(項目, 原稿の値, データベースの値, 重大度) のリスト。

    同じ文献の記録が複数ある場合、どれか1つでも原稿と合う項目は指摘しない（版の違いを誤りとしない）。
    - 年：2年以上ずれた場合だけ（会議版とプレプリントの1年の違いは誤りとしない）
    - 巻・ページ：DOI で特定した記録と比べる場合だけ（タイトル検索の候補は版の違いが混ざるため）
    - タイトル検索で見つけた記録が1つのデータベースにしか無い場合は、根拠が弱いので指摘しない
      （有名な論文の再掲・転載の記録だけが見つかることがあるため）
    """
    if not records:
        return []
    if not by_doi and len({r.source for r in records}) < 2:
        return []
    # 同じタイトル・著者の記録が別々の DOI で3つ以上ある（転載・再登録が多い有名な論文）場合は、
    # どれが原本か分からないので指摘しない（会議版とプレプリントの2つまでは通常の範囲とする）
    if not by_doi and len({r.doi.lower() for r in records if r.doi}) > MAX_DISTINCT_DOIS:
        return []
    best = records[0]
    diffs: list[tuple[str, str, str, Severity]] = []
    years = [r.year for r in records if r.year]
    if ref.year and years and all(abs(ref.year - y) >= 2 for y in years):
        diffs.append(("year", str(ref.year), str(best.year or years[0]), "major"))
    if not any(_author_matches(ref, r) for r in records):
        diffs.append(("first_author", ref.surnames[0], best.authors[0], "major"))
    if ref.title:
        comparable = [r for r in records if r.title and is_cjk(ref.title) == is_cjk(r.title)]
        head, has_head = _split_head(ref.title)
        if comparable and all(
            title_similarity(ref.title, r.title or "") < TITLE_MISMATCH
            # データベースが副題を除いたタイトルだけを持つ場合は不一致としない
            and not (has_head and title_similarity(head, r.title or "") >= TITLE_MISMATCH)
            for r in comparable
        ):
            diffs.append(("title", ref.title, comparable[0].title or "", "suggestion"))
    if by_doi:
        if ref.volume and best.volume and normalize(ref.volume) != normalize(best.volume):
            diffs.append(("volume", ref.volume, best.volume, "suggestion"))
        ours, theirs = _first_page(ref.pages or ""), _first_page(best.pages or "")
        if ours is not None and theirs is not None and ours != theirs:
            diffs.append(("pages", ref.pages or "", best.pages or "", "suggestion"))
    return diffs


def format_record(record: BibResult) -> str:
    authors = ", ".join(record.authors[:6]) + (" et al." if len(record.authors) > 6 else "")
    parts = [f"{authors} ({record.year})." if record.year else f"{authors}."]
    if record.title:
        parts.append(f"{record.title}.")
    venue = record.venue or ""
    if record.volume:
        venue += f", {record.volume}" + (f"({record.issue})" if record.issue else "")
    if record.pages:
        venue += f", pp. {record.pages}"
    if venue:
        parts.append(venue.strip(", ") + ".")
    if record.doi:
        parts.append(f"doi:{record.doi}")
    return " ".join(p for p in parts if p.strip(". "))


def short_label(ref: Reference) -> str:
    name = ref.surnames[0] if ref.surnames else (ref.authors[0] if ref.authors else "?")
    etal = " et al." if len(ref.surnames) > 1 else ""
    return f"{name}{etal}, {ref.year}" if ref.year else f"{name}{etal}"


def _novelty_claims(structures: list[ParagraphStructure]) -> list[str]:
    claims: list[str] = []
    for s in structures:
        if s.novelty_claim:
            claims.extend(c.text for c in s.claims if c.text)
            if s.summary:
                claims.append(s.summary)
    return claims[:200]


_COINED = re.compile(
    r"\b(?:[A-Z][a-z]+[A-Z][A-Za-z0-9]*|[A-Z]{2,}[A-Za-z0-9-]*|[A-Za-z]+-?\d+[A-Za-z]*)\b"
)
_JA_QUOTED = re.compile(r"[「『]([^」』]{2,30})[」』]")


def coined_terms(doc: Document, structures: Sequence[ArgumentStructure]) -> list[str]:
    """原稿で定義された造語・手法名の候補（タイトルと新規性の主張に出てくる固有の表記）。"""
    sources = [doc.title or ""]
    sources += [c.text for s in structures if s.novelty_claim for c in s.claims]
    terms: set[str] = set()
    for text in sources:
        terms.update(m.group(0) for m in _COINED.finditer(text))
        terms.update(m.group(1) for m in _JA_QUOTED.finditer(text))
    return sorted(terms)[:200]


def build_guard(doc: Document, structures: list[ParagraphStructure]) -> dict[str, object]:
    """gateway に登録する、原稿の照合情報。参考文献の節は本文に含めない。"""
    reference_sections = {s.section_id for s in doc.sections if s.role == "references"}
    body = [s.text for s in doc.sentences if s.section_id not in reference_sections][:5000]
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "body": body,
        "novelty_claims": _novelty_claims(structures),
        "coined_terms": coined_terms(doc, structures),
    }


async def _notify(callback: ProgressCallback | None, *args: object) -> None:
    if callback is None:
        return
    result = callback(*args)  # type: ignore[arg-type]
    if inspect.isawaitable(result):
        await result


class _Tally:
    def __init__(self) -> None:
        self.pending = 0
        self.blocked: dict[str, int] = {}
        self.failed = 0

    def warnings(self) -> list[str]:
        out = []
        if self.pending:
            out.append(
                f"承認待ちの外部要求が {self.pending} 件あります。承認キューで承認してから解析し直すと反映されます"
            )
        for reason, n in self.blocked.items():
            out.append(f"検索ゲートウェイが外部要求を {n} 件止めました（{reason}）")
        if self.failed:
            out.append(f"書誌データベースへの問い合わせに {self.failed} 件失敗しました")
        return out


async def _submit(
    gateway: GatewayClient,
    analysis_id: str,
    doc_id: str,
    request: BibLookup | OATextFetch,
    tally: _Tally,
) -> list[BibResult] | None:
    try:
        response = await gateway.submit(analysis_id, doc_id, request)
    except GatewayError:
        tally.failed += 1
        return None
    if response.decision in ("sent", "cached", "approved") and response.response is not None:
        return response.response.results
    if response.decision == "pending":
        tally.pending += 1
    elif response.decision == "blocked":
        reason = response.reason or "理由不明"
        tally.blocked[reason] = tally.blocked.get(reason, 0) + 1
    else:
        tally.failed += 1
    return None


def support_messages(
    *,
    sentence: str,
    context: str,
    reference: str,
    abstract: str,
    source: str,
    prompt_lang: Lang,
    output_language: str,
) -> list[dict[str, str]]:
    """引用内容の支持判定のプロンプト。アブストラクト（外部データ）は <external_data> で囲む。"""
    return [
        {
            "role": "system",
            "content": load_prompt(
                "citation_support",
                prompt_lang,
                external_data_notice=external_data_notice(prompt_lang),
                output_language=output_language,
            ),
        },
        {
            "role": "user",
            "content": load_prompt(
                "citation_support_input",
                prompt_lang,
                sentence=sentence,
                context=context,
                reference=reference,
                abstract=wrap_external(abstract, source=source),
            ),
        },
    ]


def _ref_location(
    ref: Reference, citing: dict[str, list[Sentence]]
) -> tuple[list[str], list[BBox]] | None:
    sentences = citing.get(ref.ref_id, [])
    if ref.bboxes:
        return [s.sentence_id for s in sentences], ref.bboxes
    if sentences:
        return [s.sentence_id for s in sentences], sentences[0].bboxes
    return None


async def check_citations(
    doc: Document,
    structures: list[ParagraphStructure],
    client: OitomaClient,
    gateway: GatewayClient,
    analysis_id: str,
    *,
    prompt_lang: Lang,
    output_lang: Lang,
    output_language: str,
    max_support_checks: int = 60,
    concurrency: int = 8,
    on_progress: ProgressCallback | None = None,
) -> CitationResult:
    result = CitationResult()
    tally = _Tally()
    try:
        await gateway.register(build_guard(doc, structures))
    except GatewayError as exc:
        result.warnings.append(f"引用の検証を行えませんでした（{exc}）")
        return result

    sentences = {s.sentence_id: s for s in doc.sentences}
    citing: dict[str, list[Sentence]] = {}
    for c in doc.citations:
        for rid in c.ref_ids:
            citing.setdefault(rid, []).append(sentences[c.sentence_id])
    semaphore = asyncio.Semaphore(concurrency)
    cited_refs = [r for r in doc.references if r.ref_id in citing or r.doi]
    total = len(cited_refs)
    done = 0
    await _notify(on_progress, "citation", done, total)

    # 1. 書誌の照合
    doi_of: dict[str, str] = {}

    async def verify(ref: Reference) -> None:
        nonlocal done
        title = lookup_title(ref)
        request: BibLookup | None = None
        try:
            if ref.doi:
                request = BibLookup(kind="bib_lookup", doi=ref.doi)
            elif title:
                first = ref.surnames[0][:100] if ref.surnames else None
                request = BibLookup(
                    kind="bib_lookup", title=title, first_author=first, year=ref.year
                )
        except ValidationError:
            request = None
        if request is not None:
            async with semaphore:
                candidates = await _submit(gateway, analysis_id, doc.doc_id, request, tally)
            if candidates is not None:
                _judge_record(ref, candidates, citing, result, output_lang, doi_of)
        done += 1
        await _notify(on_progress, "citation", done, total)

    await asyncio.gather(*(verify(r) for r in cited_refs))

    # 2. 引用内容の支持
    pairs: list[tuple[str, str]] = []  # (citation_id, ref_id)
    for c in doc.citations:
        for rid in c.ref_ids:
            if rid in doi_of and len(pairs) < max_support_checks:
                pairs.append((c.citation_id, rid))
    abstracts: dict[str, asyncio.Task[str | None]] = {}

    async def fetch_abstract(doi: str) -> str | None:
        async with semaphore:
            found = await _submit(
                gateway, analysis_id, doc.doc_id, OATextFetch(kind="oa_fetch", doi=doi), tally
            )
        return next((r.abstract for r in found or [] if r.abstract), None)

    refs = {r.ref_id: r for r in doc.references}
    citations = {c.citation_id: c for c in doc.citations}
    order = [s.sentence_id for s in doc.sentences]
    judgements: dict[tuple[str, str], SupportJudgement] = {}
    total += len(pairs)

    async def judge(citation_id: str, ref_id: str) -> None:
        nonlocal done
        citation = citations[citation_id]
        sentence = sentences[citation.sentence_id]
        doi = doi_of[ref_id]
        if doi not in abstracts:
            abstracts[doi] = support_tasks.create_task(fetch_abstract(doi))
        abstract = await abstracts[doi]
        if abstract:
            i = order.index(sentence.sentence_id)
            context = " ".join(
                sentences[order[j]].text for j in range(max(0, i - 1), min(len(order), i + 2))
            )
            messages = support_messages(
                sentence=sentence.text,
                context=context,
                reference=short_label(refs[ref_id]),
                abstract=abstract,
                source=doi,
                prompt_lang=prompt_lang,
                output_language=output_language,
            )
            try:

                def validate(judgement: SupportJudgement) -> SupportJudgement:
                    if judgement.evidence and not quote_matches(judgement.evidence, [abstract]):
                        raise ValueError(
                            "引用先の根拠をアブストラクトからそのまま抜き出してください"
                        )
                    if judgement.verdict == "does_not_support" and not judgement.evidence:
                        raise ValueError("支持しないと判定した根拠の原文を指定してください")
                    return judgement

                judgements[(citation_id, ref_id)] = await client.chat_json(
                    ROLE_SUPPORT, messages, SupportJudgement, lang=prompt_lang, validate=validate
                )
            except OitomaError as exc:
                result.warnings.append(f"引用内容の判定に失敗しました：{exc}")
        done += 1
        await _notify(on_progress, "citation", done, total)

    async with asyncio.TaskGroup() as support_tasks:
        for citation_id, ref_id in pairs:
            support_tasks.create_task(judge(citation_id, ref_id))

    model = client.config.role(ROLE_SUPPORT).model if judgements else "-"
    by_citation: dict[str, list[tuple[str, SupportJudgement]]] = {}
    for (cid, rid), j in judgements.items():
        by_citation.setdefault(cid, []).append((rid, j))
    for cid, items in by_citation.items():
        citation = citations[cid]
        judged_refs = {rid for rid, _ in items}
        # 引用している文献がすべて判定でき、すべてが「支持しない」ときだけ指摘する
        if judged_refs != set(citation.ref_ids) or any(
            j.verdict != "does_not_support" for _, j in items
        ):
            continue
        sentence = sentences[citation.sentence_id]
        labels = "; ".join(short_label(refs[rid]) for rid, _ in items)
        result.drafts.append(
            FindingDraft(
                subcategory="citation_unsupported",
                severity="major",
                sentence_ids=[sentence.sentence_id],
                bboxes=sentence.bboxes,
                quote=sentence.text,
                issue=template("citation_unsupported.issue", output_lang, reference=labels),
                rationale=" ".join(j.reason for _, j in items),
                evidence=[
                    Evidence(type="external", ref=doi_of[rid], excerpt=(j.evidence or "")[:300])
                    for rid, j in items
                ],
                model=model,
            )
        )
    result.warnings.extend(tally.warnings())
    return result


def _judge_record(
    ref: Reference,
    candidates: list[BibResult],
    citing: dict[str, list[Sentence]],
    result: CitationResult,
    lang: Lang,
    doi_of: dict[str, str],
) -> None:
    location = _ref_location(ref, citing)
    records = matching_records(ref, candidates)
    record = records[0] if records else None
    if record is None:
        # データベースに無いだけの文献（書籍・技術報告など）は多いので、DOI が解決できないときだけ指摘する
        if ref.doi and location is not None:
            sentence_ids, boxes = location
            result.drafts.append(
                FindingDraft(
                    subcategory="bib_not_found",
                    severity="suggestion",
                    sentence_ids=sentence_ids,
                    bboxes=boxes,
                    quote=ref.raw or ref.title or ref.doi,
                    issue=template("bib_not_found.issue", lang, doi=ref.doi),
                    rationale=template("bib_not_found.rationale", lang),
                )
            )
        return
    if record.doi:
        doi_of[ref.ref_id] = record.doi
    by_doi = bool(ref.doi and record.doi and ref.doi.lower() == record.doi.lower())
    diffs = compare(ref, records, by_doi=by_doi)
    if not diffs or location is None:
        return
    sentence_ids, boxes = location
    fields = (
        "、".join(template(f"field.{f}", lang) for f, *_ in diffs)
        if lang == "ja"
        else ", ".join(template(f"field.{f}", lang) for f, *_ in diffs)
    )
    details = ("；" if lang == "ja" else "; ").join(
        template("detail", lang, field=template(f"field.{f}", lang), ours=o, theirs=t)
        for f, o, t, _ in diffs
    )
    severity: Severity = "major" if any(s == "major" for *_, s in diffs) else "suggestion"
    result.drafts.append(
        FindingDraft(
            subcategory="bib_mismatch",
            severity=severity,
            sentence_ids=sentence_ids,
            bboxes=boxes,
            quote=ref.raw or ref.title or "",
            issue=template("bib_mismatch.issue", lang, fields=fields),
            rationale=template("bib_mismatch.rationale", lang, details=details),
            evidence=[
                Evidence(
                    type="external",
                    ref=f"{record.source}:{record.doi or ''}",
                    excerpt=format_record(record),
                )
            ],
            suggestions=[format_record(record)],
        )
    )
