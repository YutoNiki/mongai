import type { AnalysisRecord } from "../lib/api";
import { STAGES, STAGE_LABELS, label } from "../lib/labels";
import { ProgressBar } from "./ProgressBar";

/** 解析の進捗（工程ごと）。SPEC 6.5「待たせすぎない」 */
export function ProgressPanel({ analysis }: { analysis: AnalysisRecord }) {
  const current = analysis.progress.stage;
  const currentIndex = current ? STAGES.indexOf(current as (typeof STAGES)[number]) : -1;
  return (
    <section className="panel progress" aria-live="polite" aria-labelledby="progress-title">
      <h2 id="progress-title" className="panel-title">
        解析の進捗
      </h2>
      {analysis.status === "queued" && <p>順番を待っています。</p>}
      <ProgressBar status={analysis.status} progress={analysis.progress} />
      <ol className="progress-steps">
        {STAGES.map((stage, i) => {
          const state = i < currentIndex ? "done" : i === currentIndex ? "current" : "todo";
          const detail =
            state === "current" && analysis.progress.total > 0
              ? `（${analysis.progress.done} / ${analysis.progress.total}）`
              : state === "done"
                ? "（完了）"
                : "";
          return (
            <li key={stage} className={`step-${state}`}>
              <span aria-hidden="true">{state === "done" ? "✓" : state === "current" ? "▶" : "・"}</span>{" "}
              {label(STAGE_LABELS, stage)}
              {detail}
            </li>
          );
        })}
      </ol>
      <p className="note">確定した指摘から順に下の一覧に表示されます。文書の長さや確認項目によって、解析には数十分かかることがあります。</p>
    </section>
  );
}
