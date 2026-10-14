/**
 * デモの解析の指摘のステータス。デモは全員で共有するのでサーバーには保存せず（API は変更を 403 にする）、
 * このタブの中だけで変えられるようにする（sessionStorage。使えない環境ではメモリに置き、画面を閉じると消える）。
 */

import type { Finding } from "./api";

type Override = Pick<Finding, "status" | "dismiss_reason">;

const PREFIX = "mongai:demo-status:";
const memory = new Map<string, Record<string, Override>>();

function load(analysisId: string): Record<string, Override> {
  try {
    const raw = window.sessionStorage.getItem(PREFIX + analysisId);
    if (raw) return JSON.parse(raw) as Record<string, Override>;
  } catch {
    // sessionStorage が使えないときはメモリの値を使う
  }
  return memory.get(analysisId) ?? {};
}

function save(analysisId: string, overrides: Record<string, Override>): void {
  memory.set(analysisId, overrides);
  try {
    window.sessionStorage.setItem(PREFIX + analysisId, JSON.stringify(overrides));
  } catch {
    // 保存できなくても、メモリの値でこのページの表示は続けられる
  }
}

/** このタブで変えたデモの指摘のステータスを重ねる（変えていない解析はそのまま返す） */
export function applyDemoStatus(analysisId: string, findings: Finding[]): Finding[] {
  const overrides = load(analysisId);
  return findings.map((f) => {
    const o = overrides[f.finding_id];
    return o ? { ...f, ...o } : f;
  });
}

/** このタブで未対応以外に変えたデモの指摘の ID（注釈付きPDFに書き込まない指摘として API に渡す） */
export function demoHiddenFindings(analysisId: string): string[] {
  return Object.entries(load(analysisId))
    .filter(([, o]) => o.status !== "open")
    .map(([id]) => id);
}

/** このタブで変えたデモの指摘のステータスをすべて元に戻す */
export function clearDemoStatus(analysisId: string): void {
  memory.delete(analysisId);
  try {
    window.sessionStorage.removeItem(PREFIX + analysisId);
  } catch {
    // sessionStorage が使えないときはメモリの値だけを消す
  }
}

/** デモの指摘のステータスを、このタブの中だけで変える */
export function setDemoStatus(analysisId: string, finding: Finding, status: Finding["status"], reason?: string): Finding {
  const override: Override = { status, dismiss_reason: status === "dismissed" ? (reason ?? null) : null };
  save(analysisId, { ...load(analysisId), [finding.finding_id]: override });
  return { ...finding, ...override };
}
