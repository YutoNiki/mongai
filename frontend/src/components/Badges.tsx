import { CATEGORY_LABELS, SEVERITY_LABELS, SHAPE_LABELS, STATUS_LABELS, label } from "../lib/labels";
import { annotationColors } from "../theme/palette";

/** カテゴリ：色の帯に加え、名前と注釈の形を文字で示す（色だけで区別しない） */
export function CategoryBadge({ category }: { category: string }) {
  return (
    <span className="badge" style={{ borderLeftColor: annotationColors[category] ?? "#000" }}>
      {label(CATEGORY_LABELS, category)}
      <span className="badge-sub">（{label(SHAPE_LABELS, category)}）</span>
    </span>
  );
}

const SEVERITY_MARKS: Record<string, string> = { critical: "▲▲", major: "▲", suggestion: "△" };

export function SeverityBadge({ severity }: { severity: string }) {
  return (
    <span className={`badge severity-${severity}`}>
      <span aria-hidden="true">{SEVERITY_MARKS[severity] ?? ""} </span>
      {label(SEVERITY_LABELS, severity)}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge status-${status}`}>{label(STATUS_LABELS, status)}</span>;
}
