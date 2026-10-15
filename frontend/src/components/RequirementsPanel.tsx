import { useEffect, useState } from "react";

import { api, type AnalysisRecord } from "../lib/api";
import { DOCUMENT_LABELS, VERDICT_LABELS, type RequirementsResponse } from "../lib/instructions";
import { Link } from "../lib/router";

export function RequirementsPanel({ analysis }: { analysis: AnalysisRecord }) {
  const [data, setData] = useState<RequirementsResponse | null>(null);
  const [error, setError] = useState(false);
  const snapshot = analysis.settings.instruction_snapshot;
  const hasSnapshot = !!snapshot;
  const active = analysis.status === "queued" || analysis.status === "running";
  useEffect(() => {
    if (!hasSnapshot) return;
    let live = true;
    const load = () => {
      api.requirements(analysis.analysis_id).then((result) => {
        if (live) { setData(result); setError(false); }
      }, () => { if (live) setError(true); });
    };
    load();
    const timer = active ? window.setInterval(load, 3000) : null;
    return () => { live = false; if (timer !== null) window.clearInterval(timer); };
  }, [analysis.analysis_id, active, hasSnapshot]);
  const documentType = analysis.settings.document_type ?? "paper";
  if (!snapshot && documentType === "paper") return null;
  const requirements = snapshot?.requirements.filter((r) => r.enabled) ?? [];
  return <section className="panel requirements-panel" aria-labelledby="requirements-title">
    <h2 id="requirements-title">提出要件の確認</h2>
    <p>文書の種類：<strong>{DOCUMENT_LABELS[documentType]}</strong></p>
    {!snapshot ? <p>追加の課題・提出要件は指定されていません。</p> : <>
      <p className="note">解析開始時に確定した条件です。設定を後から編集・削除しても、この解析の適用条件は変わりません。</p>
      {snapshot.profiles.length > 0 && <p>適用した設定：{snapshot.profiles.map((p) => `${p.name}（版${p.revision}）`).join("、")}</p>}
      {snapshot.excluded_rule_ids.length > 0 && <p className="note">今回除外した既存ルール：{snapshot.excluded_rule_ids.join("、")}</p>}
      {data && <p aria-live="polite">
        {Object.entries(VERDICT_LABELS).map(([key, text]) => `${text} ${data.results.filter((r) => r.state === "completed" && r.verdict === key).length}件`).join(" ／ ")}
        {data.results.some((r) => r.state === "pending") && ` ／ 確認待ち ${data.results.filter((r) => r.state === "pending").length}件`}
      </p>}
      {error && <p role="alert" className="error">要件の確認結果を取得できません。ページを読み込み直してください。</p>}
      {requirements.length === 0 ? <p>追加の確認項目はありません。</p> : <div className="table-scroll">
        <table className="data-table responsive requirements-table" aria-label="提出要件ごとの判定">
          <thead><tr><th scope="col">要件</th><th scope="col">判定</th><th scope="col">理由・根拠</th></tr></thead>
          <tbody>{requirements.map((req) => {
            const result = data?.results.find((r) => r.requirement_id === req.requirement_id);
            return <tr key={req.requirement_id}>
              <th scope="row" data-label="要件"><div>{req.text}<span className="note requirement-source">{req.strength === "required" ? "必須" : "推奨"}・{req.source}</span></div></th>
              <td data-label="判定"><span>{!result ? "取得中" : result.state === "pending" ? "確認待ち" : VERDICT_LABELS[result.verdict]}</span></td>
              <td data-label="理由・根拠"><div>{result && <>
                <p>{result.reason}</p>
                {result.quote && <blockquote>{result.quote}</blockquote>}
                {result.suggestion && <p>改善の方向：{result.suggestion}</p>}
                {result.finding_id ? <Link href={`/analyses/${analysis.analysis_id}/findings/${result.finding_id}`}>PDFの該当箇所を見る</Link> :
                  result.pages.length > 0 && <p className="note">確認箇所：{result.pages.join("、")}ページ</p>}
                {result.state === "completed" && <p className="note">確認範囲：{result.coverage === "full" ? "抽出テキスト全体" : result.coverage === "partial" ? "抽出テキストの一部" : "未確認・自動判定の対象外"}</p>}
              </>}</div></td>
            </tr>;
          })}</tbody>
        </table>
      </div>}
      <p className="note">文字数はPDFの抽出テキストに基づきます。「判断できない」は条件を満たしたことを意味しません。前回比較は文書種別と適用条件が一致する解析を対象にします。</p>
      <div className="status-buttons">
        <a className="button-secondary" href={api.requirementsUrl(analysis.analysis_id, "csv")}>要件の確認結果（CSV）</a>
        <a className="button-secondary" href={api.requirementsUrl(analysis.analysis_id, "json")}>要件の確認結果（JSON）</a>
      </div>
    </>}
  </section>;
}
