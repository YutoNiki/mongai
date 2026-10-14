/**
 * 解析結果の先読み。トップページで論文の行に触れた（マウスを乗せた・指を置いた・フォーカスした）時点で、
 * 画面の部品と解析結果のデータを取り始め、スライドしてきた画面に最初から中身を出す。
 * 解析結果はメモリにだけ置き、短い時間で捨てる（端末には保存しない）。
 */

import { api, type AnalysisSummary, type Finding } from "./api";
import { applyDemoStatus } from "./demoStatus";
import { prefetchDashboard } from "./pages";

export interface AnalysisData {
  summary: AnalysisSummary;
  findings: Finding[];
}

const FRESH_MS = 30_000;
const WAIT_MS = 400;

const pending = new Map<string, { at: number; promise: Promise<AnalysisData> }>();
const ready = new Map<string, { at: number; data: AnalysisData }>();

export function prefetchAnalysis(analysisId: string): Promise<AnalysisData> {
  prefetchDashboard();
  const now = Date.now();
  const current = pending.get(analysisId);
  if (current && now - current.at < FRESH_MS) return current.promise;
  const promise = Promise.all([api.summary(analysisId), api.findings(analysisId)]).then(([summary, findings]) => {
    const data = { summary, findings };
    ready.set(analysisId, { at: Date.now(), data });
    return data;
  });
  promise.catch(() => pending.delete(analysisId));
  pending.set(analysisId, { at: now, promise });
  return promise;
}

/** 先読み済みで新しいデータがあれば返す（無ければ null。画面は通常どおり読み込む） */
export function prefetched(analysisId: string): AnalysisData | null {
  const entry = ready.get(analysisId);
  if (!entry || Date.now() - entry.at >= FRESH_MS) return null;
  // 先読みの後に、詳細画面でデモの指摘のステータスを変えた場合に備えて重ね直す
  return { ...entry.data, findings: applyDemoStatus(analysisId, entry.data.findings) };
}

/** 先読みの完了を少しだけ待つ（遅いときは待たずに進み、画面側で読み込む） */
export async function waitForAnalysis(analysisId: string): Promise<void> {
  await Promise.race([
    prefetchAnalysis(analysisId).catch(() => undefined),
    new Promise((resolve) => window.setTimeout(resolve, WAIT_MS)),
  ]);
}
