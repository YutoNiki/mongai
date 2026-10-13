import type { Progress } from "./api";
import { STAGES } from "./labels";

/**
 * 工程ごとの所要時間の目安（全体を100とした割合）。2026-10 の解析の記録で、段落内のチェックが
 * 大半を占めていたことによる。進捗バーの見た目の目安で、正確な残り時間ではない。
 */
const STAGE_WEIGHTS: Record<(typeof STAGES)[number], number> = {
  ingest: 5,
  structure: 8,
  paragraph_check: 60,
  flow_check: 5,
  citation: 10,
  suggestion: 10,
  locate: 2,
};

/** 解析全体の進み具合（0〜100 の整数）。完了は 100、順番待ち・不明は 0。 */
export function overallPercent(status: string, progress: Progress): number {
  if (status === "completed") return 100;
  const index = progress.stage ? STAGES.indexOf(progress.stage as (typeof STAGES)[number]) : -1;
  const current = STAGES[index];
  if (status === "queued" || current === undefined) return 0;
  const before = STAGES.slice(0, index).reduce((sum, stage) => sum + STAGE_WEIGHTS[stage], 0);
  const fraction = progress.total > 0 ? Math.min(progress.done / progress.total, 1) : 0;
  const value = before + STAGE_WEIGHTS[current] * fraction;
  // 完了の通知の前に 100% と表示しないよう、実行中は 99 で止める
  return Math.min(Math.floor(value), 99);
}
