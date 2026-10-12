/** ダッシュボードの集計を文章にする（グラフの代替テキスト・要約。SPEC 6.6）。 */

import type { Counts } from "./api";
import { CATEGORY_LABELS, SEVERITIES, SEVERITY_LABELS, label } from "./labels";

export function diffText(current: number, previous: number | undefined): string | null {
  if (previous === undefined) return null;
  const diff = current - previous;
  if (diff === 0) return "前回比 ±0件";
  return `前回比 ${diff > 0 ? "+" : "−"}${Math.abs(diff)}件`;
}

export function summaryText(counts: Counts, previousTotal?: number): string {
  if (counts.total === 0) return "指摘はありません。";
  const severity = SEVERITIES.map((s) => `${label(SEVERITY_LABELS, s)} ${counts.by_severity[s] ?? 0}件`).join("、");
  const parts = [`指摘は全部で${counts.total}件です（${severity}）。`];
  const topCategory = Object.entries(counts.by_category).sort((a, b) => b[1] - a[1])[0];
  if (topCategory && topCategory[1] > 0) {
    parts.push(`カテゴリ別では「${label(CATEGORY_LABELS, topCategory[0])}」が${topCategory[1]}件で最も多くなっています。`);
  }
  const topSection = [...counts.by_section].sort((a, b) => b.count - a.count)[0];
  if (topSection && topSection.count > 0) {
    parts.push(`セクション別では「${topSection.label}」が${topSection.count}件で最も多くなっています。`);
  }
  const diff = diffText(counts.total, previousTotal);
  if (diff) parts.push(`${diff}です。`);
  return parts.join("");
}

/** CSV の1行。値に区切り文字・引用符・改行があれば引用符で囲む */
export function csvRow(values: (string | number)[]): string {
  return values
    .map((v) => {
      const text = String(v);
      return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
    })
    .join(",");
}

export function toCsv(header: string[], rows: (string | number)[][]): string {
  // Excel で文字化けしないよう BOM を付ける
  return "﻿" + [header, ...rows].map(csvRow).join("\r\n") + "\r\n";
}
