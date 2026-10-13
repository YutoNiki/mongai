"""テキストの要約レポート（SPEC 3.5・6.6）。主要な問題と件数を文章でまとめる（アクセシビリティ対応を兼ねる）。"""

from core.analyze.models import SEVERITY_RANK, Finding
from core.annotate.labels import category_label, severity_label, subcategory_label
from core.api.schemas import Counts
from core.report.diff import FindingsDiff

SEVERITY_ORDER = ("critical", "major", "suggestion")
STATUS_LABELS = {"open": "未対応", "accepted": "対応済み", "dismissed": "却下"}
# 「主な指摘」に載せる重大度
MAIN_SEVERITIES = frozenset({"critical", "major"})


def _signed(value: int) -> str:
    return "±0" if value == 0 else f"{'+' if value > 0 else '−'}{abs(value)}"


def text_report(
    *,
    title: str | None,
    filename: str | None,
    analysis_id: str,
    created_at: str,
    counts: Counts,
    findings: list[Finding],
    previous_total: int | None = None,
    diff: FindingsDiff | None = None,
) -> str:
    lines = ["門外不出（MongAI）解析レポート", ""]
    lines.append(f"論文：{title or '（タイトル不明）'}" + (f"（{filename}）" if filename else ""))
    lines.append(f"解析ID：{analysis_id}")
    lines.append(f"解析日時：{created_at}")
    lines.append("")

    severity = "、".join(
        f"{severity_label(s)} {counts.by_severity.get(s, 0)}件" for s in SEVERITY_ORDER
    )
    total = f"指摘は全部で{counts.total}件です（{severity}）。"
    if previous_total is not None:
        total += f"前回比 {_signed(counts.total - previous_total)}件です。"
    lines.append("■ 概要")
    lines.append(total)
    categories = sorted(
        ((c, n) for c, n in counts.by_category.items() if n > 0), key=lambda x: -x[1]
    )
    if categories:
        lines.append(
            "カテゴリ別：" + "、".join(f"{category_label(c)} {n}件" for c, n in categories)
        )
    sections = [s for s in counts.by_section if s.count > 0]
    if sections:
        lines.append(
            "セクション別（論文内の順）：" + "、".join(f"{s.label} {s.count}件" for s in sections)
        )
    if diff is not None:
        lines.append(
            f"前回との差分：解消 {len(diff.resolved)}件、新規 {len(diff.new)}件、"
            f"継続 {len(diff.persisting)}件。"
        )
    lines.append("")

    active = [f for f in findings if f.status != "dismissed"]
    main = sorted(
        (f for f in active if f.severity in MAIN_SEVERITIES),
        key=lambda f: (SEVERITY_RANK[f.severity], f.page, f.finding_id),
    )
    lines.append(f"■ 主な指摘（重大・要修正、{len(main)}件）")
    if not main:
        lines.append("重大・要修正の指摘はありません。")
    for n, f in enumerate(main, start=1):
        kind = f"{category_label(f.category)}・{subcategory_label(f.subcategory)}"
        lines.append(
            f"{n}. [{f.finding_id} {kind}・{severity_label(f.severity)}・"
            f"{STATUS_LABELS[f.status]}] {f.page}ページ"
        )
        lines.append(f"   箇所：「{f.quote}」")
        lines.append(f"   指摘：{f.issue}")
        for s in f.suggestions:
            lines.append(f"   改善案：{s}")
    rest = len(active) - len(main)
    if rest > 0:
        lines.append(f"ほかに、提案の指摘が{rest}件あります（指摘の一覧・CSV を参照）。")

    if diff is not None and diff.resolved:
        lines.append("")
        lines.append(f"■ 前回から解消された指摘（{len(diff.resolved)}件）")
        for f in diff.resolved:
            lines.append(f"- {subcategory_label(f.subcategory)}：「{f.quote}」")
    return "\n".join(lines) + "\n"
