/** 詳細画面：PDF ビューア＋指摘の詳細（ダッシュボードから1クリック） */

import { useCallback, useEffect, useMemo, useState } from "react";

import { CategoryBadge, SeverityBadge, StatusBadge } from "../components/Badges";
import { sortFindings } from "../components/FindingsTable";
import { PdfViewer } from "../components/PdfViewer";
import { api, type AnalysisSummary, type Finding } from "../lib/api";
import { CATEGORY_LABELS, SUBCATEGORY_LABELS, label } from "../lib/labels";
import { Link, navigate } from "../lib/router";
import { annotationColors } from "../theme/palette";

export function DetailPage({ analysisId, findingId }: { analysisId: string; findingId: string }) {
  const [summary, setSummary] = useState<AnalysisSummary | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [page, setPage] = useState(1);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, f] = await Promise.all([api.summary(analysisId), api.findings(analysisId)]);
      setSummary(s);
      setFindings(sortFindings(f));
    } catch (e) {
      setError(e instanceof Error ? e.message : "読み込めませんでした");
    }
  }, [analysisId]);
  useEffect(() => {
    void load();
  }, [load]);

  const index = findings.findIndex((f) => f.finding_id === findingId);
  const finding = index >= 0 ? findings[index] : undefined;
  // 表示する指摘が変わったときだけ、ページ・理由・メッセージを初期化する。
  // （ステータスの更新で指摘のデータが新しくなっただけでは初期化しない。更新の結果のメッセージが消えるため）
  const [shownId, setShownId] = useState<string | null>(null);
  if (finding && finding.finding_id !== shownId) {
    setShownId(finding.finding_id);
    setPage(finding.page);
    setReason(finding.dismiss_reason ?? "");
    setMessage(null);
  }

  const neighbors = useMemo(
    () => ({ prev: findings[index - 1]?.finding_id, next: findings[index + 1]?.finding_id }),
    [findings, index],
  );

  if (error) return <p role="alert" className="error page">{error}</p>;
  if (!summary) return <p className="page">読み込み中…</p>;
  if (!finding) return <p className="page">指摘 {findingId} が見つかりません。</p>;

  const update = async (status: Finding["status"]) => {
    if (status === "dismissed" && !reason.trim()) {
      setMessage("却下する理由を入力してください");
      return;
    }
    try {
      const updated = await api.updateStatus(analysisId, finding.finding_id, status, status === "dismissed" ? reason : undefined);
      setFindings((list) => list.map((f) => (f.finding_id === updated.finding_id ? updated : f)));
      setMessage("ステータスを更新しました");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "更新できませんでした");
    }
  };

  const color = annotationColors[finding.category] ?? "#000";
  return (
    <div className="page detail">
      <nav aria-label="パンくず" className="breadcrumb">
        <Link href={`/analyses/${analysisId}`}>ダッシュボードに戻る</Link>
      </nav>
      <a className="button-secondary mobile-only" href="#pdf-viewer">
        PDF の該当箇所を見る
      </a>
      <div className="detail-grid">
        <PdfViewer
          url={api.paperUrl(summary.analysis.doc_id)}
          page={page}
          boxes={finding.bboxes}
          color={color}
          label={`${finding.finding_id} ${label(CATEGORY_LABELS, finding.category)}`}
          onPageChange={setPage}
        />
        <section className="panel finding-detail" aria-labelledby="finding-title">
          <h1 id="finding-title">
            {finding.finding_id} {label(SUBCATEGORY_LABELS, finding.subcategory)}
          </h1>
          <p className="badges">
            <SeverityBadge severity={finding.severity} /> <CategoryBadge category={finding.category} />{" "}
            <StatusBadge status={finding.status} />
          </p>
          <dl className="finding-fields">
            <dt>該当箇所（{finding.page}ページ）</dt>
            <dd>
              <blockquote>{finding.quote}</blockquote>
              {page !== finding.page && (
                <button type="button" className="button-secondary" onClick={() => setPage(finding.page)}>
                  該当箇所のページを表示
                </button>
              )}
            </dd>
            <dt>指摘</dt>
            <dd>{finding.issue}</dd>
            <dt>理由</dt>
            <dd>{finding.rationale}</dd>
            {finding.evidence.length > 0 && (
              <>
                <dt>根拠（原稿内の文）</dt>
                <dd>
                  <ul>
                    {finding.evidence.map((e) => (
                      <li key={e.ref}>{e.excerpt}</li>
                    ))}
                  </ul>
                </dd>
              </>
            )}
            <dt>改善案</dt>
            <dd>
              {finding.suggestions.length > 0 ? (
                <ol>
                  {finding.suggestions.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ol>
              ) : (
                "（なし）"
              )}
            </dd>
            <dt>確度</dt>
            <dd>
              {finding.confidence === "high" ? "高" : "低"}（{finding.models.primary} が {finding.samples} 回中 {finding.votes} 回指摘
              {finding.models.secondary &&
                `。第2のモデル ${finding.models.secondary} は${finding.models.agreed ? "問題と認めた" : "問題と認めなかった"}`}
              ）
            </dd>
          </dl>

          <fieldset className="status-form">
            <legend>この指摘への対応</legend>
            <label>
              却下する理由（却下する場合は必須）
              <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} maxLength={1000} />
            </label>
            <div className="status-buttons">
              <button type="button" onClick={() => void update("accepted")}>
                対応済みにする
              </button>
              <button type="button" className="button-secondary" onClick={() => void update("dismissed")}>
                却下する
              </button>
              {finding.status !== "open" && (
                <button type="button" className="button-secondary" onClick={() => void update("open")}>
                  未対応に戻す
                </button>
              )}
            </div>
            {message && <p aria-live="polite">{message}</p>}
          </fieldset>

          <nav aria-label="指摘の移動" className="pager">
            {neighbors.prev ? (
              <button type="button" className="button-secondary" onClick={() => navigate(`/analyses/${analysisId}/findings/${neighbors.prev}`)}>
                前の指摘（{neighbors.prev}）
              </button>
            ) : (
              <span />
            )}
            {neighbors.next && (
              <button type="button" className="button-secondary" onClick={() => navigate(`/analyses/${analysisId}/findings/${neighbors.next}`)}>
                次の指摘（{neighbors.next}）
              </button>
            )}
          </nav>
        </section>
      </div>
    </div>
  );
}
