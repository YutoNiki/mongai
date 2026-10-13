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
  citation: 6,
  rule_check: 4,
  double_check: 2,
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

/** 経過時間を「1時間5分」のように表す（1分未満は「1分未満」）。 */
export function formatDuration(ms: number): string {
  const minutes = Math.floor(Math.max(ms, 0) / 60_000);
  if (minutes < 1) return "1分未満";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest}分`;
  return rest === 0 ? `${hours}時間` : `${hours}時間${rest}分`;
}

/** 解析の経過時間（解析中・順番待ちは今まで、終わったものは終了まで）。日時が読めないときは null。 */
export function elapsedText(createdAt: string, finishedAt: string | null, now: number): string | null {
  const start = Date.parse(createdAt);
  const end = finishedAt ? Date.parse(finishedAt) : now;
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return formatDuration(end - start);
}
