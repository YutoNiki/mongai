import type { Progress } from "../lib/api";
import { STAGE_LABELS, label } from "../lib/labels";
import { overallPercent } from "../lib/progress";

/** 解析全体の進捗バー。色だけに頼らず、工程名と割合を文字でも示す。 */
export function ProgressBar({ status, progress }: { status: string; progress: Progress }) {
  const percent = overallPercent(status, progress);
  const stage = status === "queued" ? "順番待ち" : progress.stage ? label(STAGE_LABELS, progress.stage) : "準備中";
  const count = status === "running" && progress.total > 0 ? `（${progress.done} / ${progress.total}）` : "";
  const text = `${stage}${count}・全体の約${percent}%`;
  return (
    <div className="progress-bar">
      <div
        className="progress-bar-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={text}
      >
        <div className="progress-bar-fill" style={{ width: `${percent}%` }} />
      </div>
      <span className="progress-bar-text">{text}</span>
    </div>
  );
}
