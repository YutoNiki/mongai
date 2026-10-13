"""機械判定のルール（SPEC 3.2-4）。LLM を使わず、Python の文字列の照合で判定する。

- abbreviation_definition：略語（英大文字の並び）が、初出時に括弧で定義されているか
- figure_table_reference：図表のキャプションに対応する参照が本文にあるか、本文が存在しない図表を参照していないか
- unit_consistency：同じ単位が、論文の中で複数の表記で書かれていないか

誤検出を減らすため、確実にいえるものだけを指摘する（例：図表のキャプションを1つも検出できなかった論文では、
存在しない図表への参照は指摘しない）。
"""

import re
import unicodedata
from dataclasses import dataclass, field

from core.analyze.context import SKIPPED_ROLES
from core.analyze.models import Severity
from core.analyze.templates import text as template
from core.ingest.models import BBox, Document, Lang, Paragraph, Sentence
from core.rules.models import Checker, Rule


@dataclass
class RuleViolation:
    """ルール違反（番号を振る前。pipeline で Finding にする）。"""

    rule: Rule
    subcategory: str
    severity: Severity
    sentence_ids: list[str]
    bboxes: list[BBox]
    quote: str
    issue: str
    rationale: str
    suggestions: list[str] = field(default_factory=list)
    model: str = "mechanical"


def body_sentences(doc: Document) -> list[Sentence]:
    """本文（参考文献・謝辞・著者略歴などを除く段落）の文を、論文の順に返す。"""
    roles = {s.section_id: s.role for s in doc.sections}
    body = {
        p.paragraph_id
        for p in doc.paragraphs
        if p.kind == "body" and roles.get(p.section_id or "") not in SKIPPED_ROLES
    }
    return [s for s in doc.sentences if s.paragraph_id in body]


# ---------------------------------------------------------------- 略語の初出時の定義

_ACRONYM = re.compile(r"(?<![A-Za-z0-9\-])([A-Z][A-Z0-9\-]*[A-Z][A-Z0-9]*)s?(?![A-Za-z0-9])")
# 定義しなくてよい一般的な略語（ローマ数字を含む）
COMMON_ACRONYMS = frozenset(
    {
        "AI",
        "PDF",
        "URL",
        "DOI",
        "ID",
        "OK",
        "USA",
        "UK",
        "EU",
        "UN",
        "PC",
        "OS",
        "IT",
        "GPU",
        "CPU",
        "RAM",
        "USB",
        "HTML",
        "HTTP",
        "HTTPS",
        "API",
        "JST",
        "UTC",
        "PhD",
        "II",
        "III",
        "IV",
        "VI",
        "VII",
        "VIII",
        "IX",
        "XI",
        "XII",
        "TV",
        "CD",
        "DVD",
    }
)
MIN_LENGTH = 2
MAX_LENGTH = 10


def _defined_at(acronym: str, sentence: str) -> bool:
    """文の中で、略語が「正式名称（略語）」か「略語（正式名称）」の形で書かれているか。"""
    escaped = re.escape(acronym)
    patterns = (
        rf"[（(]\s*{escaped}s?\s*[)）,，、;；:：]",  # 正式名称（ACR）、正式名称（ACR；…）
        rf"{escaped}s?\s*[（(][^()（）]{{3,}}[)）]",  # ACR（正式名称）
    )
    return any(re.search(p, sentence) for p in patterns)


def check_abbreviations(doc: Document, rule: Rule, lang: Lang) -> list[RuleViolation]:
    sentences = body_sentences(doc)
    first: dict[str, Sentence] = {}
    defined: dict[str, int] = {}
    for index, sentence in enumerate(sentences):
        for match in _ACRONYM.finditer(sentence.text):
            acronym = match.group(1)
            if acronym in COMMON_ACRONYMS or not MIN_LENGTH <= len(acronym) <= MAX_LENGTH:
                continue
            first.setdefault(acronym, sentence)
            if acronym not in defined and _defined_at(acronym, sentence.text):
                defined[acronym] = index
    order = {s.sentence_id: i for i, s in enumerate(sentences)}
    # 参考文献に現れる略語は、引用している手法・データセットの名前（BLEU、ROUGE など）とみなす
    cited = " ".join(f"{r.title or ''} {r.raw or ''}" for r in doc.references)
    violations: list[RuleViolation] = []
    for acronym, sentence in first.items():
        where = defined.get(acronym)
        if where is not None and where <= order[sentence.sentence_id]:
            continue  # 初出で定義されている
        base = acronym.split("-")[0]
        if base != acronym and base in first:
            continue  # ROUGE-L などの派生形は、元の略語（ROUGE）で判定する
        if where is None:
            # 一度も定義されない略語は、2回以上使われ、引用している名前でないものだけを指摘する
            uses = sum(acronym in s.text for s in sentences)
            if uses < 2 or re.search(
                rf"(?<![A-Za-z]){re.escape(base)}(?![A-Za-z])", cited, re.IGNORECASE
            ):
                continue
            # 「CNN/DailyMail」のようにスラッシュでつながった名前の一部は略語として扱わない
            if all(
                re.search(rf"/{re.escape(acronym)}|{re.escape(acronym)}/", s.text)
                for s in sentences
                if acronym in s.text
            ):
                continue
        key = "abbr_late" if where is not None else "abbr_undefined"
        violations.append(
            RuleViolation(
                rule=rule,
                subcategory="abbreviation_definition",
                severity="suggestion",
                sentence_ids=[sentence.sentence_id],
                bboxes=sentence.bboxes,
                quote=acronym,
                issue=template(f"{key}.issue", lang, acronym=acronym),
                rationale=template(f"{key}.rationale", lang),
            )
        )
    return violations


# ---------------------------------------------------------------- 図表の参照

_CAPTION = re.compile(r"^\s*(図|表|Fig\.?|Figure|Table)\s*([0-9]+)\s*(.?)", re.IGNORECASE)
# 図表番号の直後がこれらで続く段落は、キャプションではなく本文（「表2 と3 に示す」「Table 5 shows」）
_NOT_CAPTION_NEXT = frozenset("とやに及およ,，、")
_REFERENCE = re.compile(
    r"(図|表|Fig\.|Figs\.|Figures?|Tables?)\s*~?\s*([0-9]+)"
    # 並んだ番号（「表2 と3」「Tables 2 and 3」「図1，2」）
    r"((?:\s*(?:と|および|及び|,|，|、|and|-|–|〜|～)\s*[0-9]+)*)",
    re.IGNORECASE,
)
# キャプションが表の数値などと一緒に取り込まれた段落の中の「Table 2:」「図3 」
_CAPTION_INSIDE = re.compile(r"(?:^|\s)(Fig\.?|Figure|Table)\s*([0-9]+)\s*:", re.IGNORECASE)


def _kind(label: str) -> str:
    label = label.lower()
    return "table" if label in ("表", "table", "tables") else "figure"


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def is_caption(text: str) -> bool:
    """図表のキャプションか。英語は "Figure 1:" のようにコロンが続くもの、日本語は番号の後が本文の続き方
    （「と」「に」など）でないもの。PDF の取り込みでキャプションとされた本文の段落を除くため。"""
    match = _CAPTION.match(_nfkc(text))
    if not match:
        return False
    following = match.group(3)
    if match.group(1).isascii():
        return following in (":", ".")
    return following not in _NOT_CAPTION_NEXT


def check_figure_references(doc: Document, rule: Rule, lang: Lang) -> list[RuleViolation]:
    captions: dict[tuple[str, int], Paragraph] = {}
    exists: set[tuple[str, int]] = set()
    # 参照を探す文：本文の文と、キャプションとされたが本文だった段落
    texts: list[tuple[str, list[str], list[BBox]]] = [
        (s.text, [s.sentence_id], s.bboxes) for s in body_sentences(doc)
    ]
    for p in doc.paragraphs:
        if p.kind != "caption":
            continue
        if is_caption(p.text):
            match = _CAPTION.match(_nfkc(p.text))
            assert match is not None
            captions.setdefault((_kind(match.group(1)), int(match.group(2))), p)
            continue
        texts.append((p.text, p.sentence_ids[:1], p.bboxes))
    # 表の数値などと一緒に本文として取り込まれたキャプション（"… Table 2: …"）も、図表の存在として数える
    for text, _, _ in texts:
        for inside in _CAPTION_INSIDE.finditer(_nfkc(text)):
            exists.add((_kind(inside.group(1)), int(inside.group(2))))
    referenced: dict[tuple[str, int], tuple[str, list[str], list[BBox]]] = {}
    for text, ids, boxes in texts:
        for match in _REFERENCE.finditer(_nfkc(text)):
            kind = _kind(match.group(1))
            numbers = [
                int(match.group(2)),
                *(int(n) for n in re.findall(r"[0-9]+", match.group(3))),
            ]
            # 「表2-4」は範囲（2、3、4）として扱う
            if re.search(r"[-–〜～]", match.group(3)) and len(numbers) == 2:
                numbers = list(range(numbers[0], numbers[1] + 1))
            for number in numbers:
                referenced.setdefault((kind, number), (text, ids, boxes))
    names = {
        "figure": "図" if lang == "ja" else "Figure",
        "table": "表" if lang == "ja" else "Table",
    }
    violations: list[RuleViolation] = []
    for (kind, number), caption in sorted(captions.items()):
        if (kind, number) in referenced or not caption.bboxes:
            continue
        label = f"{names[kind]}{number}" if lang == "ja" else f"{names[kind]} {number}"
        violations.append(
            RuleViolation(
                rule=rule,
                subcategory="figure_table_reference",
                severity="suggestion",
                sentence_ids=caption.sentence_ids[:1],
                bboxes=caption.bboxes,
                quote=caption.text[:120],
                issue=template("fig_unreferenced.issue", lang, label=label),
                rationale=template("fig_unreferenced.rationale", lang),
            )
        )
    largest: dict[str, int] = {}
    for kind, number in [*captions, *exists]:
        largest[kind] = max(largest.get(kind, 0), number)
    for (kind, number), (text, ids, boxes) in sorted(referenced.items()):
        # 番号の抜け（表1〜4と表6はあるのに表5が無い）だけを指摘する。最大の番号より大きいものは、
        # 付録などでキャプションを検出できなかった可能性があるので指摘しない
        known = (kind, number) in captions or (kind, number) in exists
        if known or number > largest.get(kind, 0) or not boxes:
            continue
        label = f"{names[kind]}{number}" if lang == "ja" else f"{names[kind]} {number}"
        violations.append(
            RuleViolation(
                rule=rule,
                subcategory="figure_table_reference",
                severity="suggestion",
                sentence_ids=ids,
                bboxes=boxes,
                quote=text,
                issue=template("fig_missing.issue", lang, label=label),
                rationale=template("fig_missing.rationale", lang),
            )
        )
    return violations


# ---------------------------------------------------------------- 単位の表記ゆれ

# 単位ごとの表記（数値の直後に現れたものだけを数える）
UNIT_VARIANTS: dict[str, tuple[str, ...]] = {
    "percent": ("%", "％", "パーセント"),
    "millisecond": ("ms", "ミリ秒"),
    "second": ("sec", "秒"),
    "kilometer": ("km", "ｋｍ", "キロメートル"),
    "kilogram": ("kg", "ｋｇ", "キログラム"),
    "celsius": ("℃", "°C", "度C"),
}
_NUMBER = r"(?<![A-Za-z])[0-9０-９]+(?:[.．][0-9０-９]+)?\s?"


def _variant_pattern(variant: str) -> re.Pattern[str]:
    # 英字の単位は、後ろに英字が続く場合（"5 msec" の ms など）を除く
    tail = r"(?![A-Za-z])" if variant[-1].isascii() and variant[-1].isalpha() else ""
    return re.compile(_NUMBER + re.escape(variant) + tail)


_PATTERNS = {
    family: [(v, _variant_pattern(v)) for v in variants]
    for family, variants in UNIT_VARIANTS.items()
}


def check_units(doc: Document, rule: Rule, lang: Lang) -> list[RuleViolation]:
    sentences = body_sentences(doc)
    violations: list[RuleViolation] = []
    for patterns in _PATTERNS.values():
        uses: dict[str, list[Sentence]] = {}
        for sentence in sentences:
            for variant, pattern in patterns:
                if pattern.search(sentence.text):
                    uses.setdefault(variant, []).append(sentence)
        if len(uses) < 2:
            continue
        # 最も多い表記を基準に、それ以外の表記の初出を指摘する
        main = max(uses, key=lambda v: len(uses[v]))
        for variant, found in uses.items():
            if variant == main:
                continue
            sentence = found[0]
            violations.append(
                RuleViolation(
                    rule=rule,
                    subcategory="unit_consistency",
                    severity="suggestion",
                    sentence_ids=[sentence.sentence_id],
                    bboxes=sentence.bboxes,
                    quote=sentence.text,
                    issue=template("unit.issue", lang, variant=variant, main=main),
                    rationale=template(
                        "unit.rationale", lang, main=main, count=str(len(uses[main]))
                    ),
                )
            )
    return violations


CHECKERS = {
    "abbreviation_definition": check_abbreviations,
    "figure_table_reference": check_figure_references,
    "unit_consistency": check_units,
}


def run_mechanical(doc: Document, rules: list[Rule], lang: Lang) -> list[RuleViolation]:
    """承認済みの機械判定のルールを実行する（同じ種類のルールが複数あっても1回だけ）。"""
    done: set[Checker] = set()
    violations: list[RuleViolation] = []
    for rule in rules:
        if rule.status != "approved" or rule.check_type != "mechanical" or rule.checker is None:
            continue
        if rule.checker in done:
            continue
        if rule.lang not in ("both", doc.lang):
            continue
        done.add(rule.checker)
        violations += CHECKERS[rule.checker](doc, rule, lang)
    return violations
