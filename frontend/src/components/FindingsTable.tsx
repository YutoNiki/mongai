import type { Finding } from "../lib/api";
import { SUBCATEGORY_LABELS, label } from "../lib/labels";
import { Link } from "../lib/router";
import { CategoryBadge, SeverityBadge, StatusBadge } from "./Badges";

const RANK: Record<string, number> = { critical: 0, major: 1, suggestion: 2 };

export function sortFindings(findings: Finding[]): Finding[] {
  return [...findings].sort(
    (a, b) => (RANK[a.severity] ?? 9) - (RANK[b.severity] ?? 9) || a.page - b.page || a.finding_id.localeCompare(b.finding_id),
  );
}

/** 指摘の一覧（重大度順）。番号をクリックすると PDF の該当箇所を開く */
export function FindingsTable({ analysisId, findings }: { analysisId: string; findings: Finding[] }) {
  if (findings.length === 0) return <p>条件に合う指摘はありません。</p>;
  return (
    <div className="table-scroll">
      <table className="data-table findings responsive">
        <caption className="visually-hidden">指摘の一覧（重大度の順）</caption>
        <thead>
          <tr>
            <th scope="col">番号</th>
            <th scope="col">重大度</th>
            <th scope="col">種類</th>
            <th scope="col" className="num">
              ページ
            </th>
            <th scope="col">指摘</th>
            <th scope="col">確度</th>
            <th scope="col">ステータス</th>
          </tr>
        </thead>
        <tbody>
          {sortFindings(findings).map((f) => (
            <tr key={f.finding_id}>
              <th scope="row" data-label="番号">
                <Link href={`/analyses/${analysisId}/findings/${f.finding_id}`}>{f.finding_id}</Link>
              </th>
              <td data-label="重大度">
                <SeverityBadge severity={f.severity} />
              </td>
              <td data-label="種類">
                <CategoryBadge category={f.category} />
                <div className="sub">{label(SUBCATEGORY_LABELS, f.subcategory)}</div>
              </td>
              <td className="num" data-label="ページ">
                {f.page}
              </td>
              <td data-label="指摘">{f.issue}</td>
              <td data-label="確度">
                {f.confidence === "high" ? "高" : "低"}
                <span className="sub">
                  （{f.samples}回中{f.votes}回）
                </span>
              </td>
              <td data-label="ステータス">
                <StatusBadge status={f.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
