/**
 * ダッシュボード（提示型、SPEC 6.3）。
 * 上部：タイトル・フィルター／左上：総指摘数と重大度の内訳（前回比）／中段：カテゴリ別・セクション別／
 * 下段：指摘の一覧／下部：メタ情報
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { BarChart } from "../components/BarChart";
import { FindingsTable } from "../components/FindingsTable";
import { ProgressPanel } from "../components/ProgressPanel";
import { api, isVoted, type AnalysisDiff, type AnalysisSummary, type Counts, type Finding } from "../lib/api";
import {
  CATEGORIES,
  CATEGORY_LABELS,
  SEVERITIES,
  SEVERITY_LABELS,
  STATUS_LABELS,
  SUBCATEGORY_LABELS,
  label,
} from "../lib/labels";
import { Link } from "../lib/router";
import { diffText, summaryText } from "../lib/summary";

interface Filters {
  category: string;
  severity: string;
  section: string;
  confidence: string;
  status: string; // "" は却下を除くすべて
}

const NO_FILTERS: Filters = { category: "", severity: "", section: "", confidence: "", status: "" };
const POLL_MS = 3000;

function applyFilters(findings: Finding[], f: Filters, sectionOf: Record<string, string>): Finding[] {
  return findings.filter(
    (x) =>
      (!f.category || x.category === f.category) &&
      (!f.severity || x.severity === f.severity) &&
      (!f.section || sectionOf[x.section_id ?? ""] === f.section) &&
      (!f.confidence || (isVoted(x) && x.confidence === f.confidence)) &&
      (f.status ? x.status === f.status : x.status !== "dismissed"),
  );
}

function countOf(findings: Finding[], base: Counts): Counts {
  const by = (key: (f: Finding) => string) =>
    findings.reduce<Record<string, number>>((acc, f) => ({ ...acc, [key(f)]: (acc[key(f)] ?? 0) + 1 }), {});
  const bySection = by((f) => base.section_of[f.section_id ?? ""] ?? "");
  return {
    ...base,
    total: findings.length,
    by_severity: Object.fromEntries(SEVERITIES.map((s) => [s, by((f) => f.severity)[s] ?? 0])),
    by_category: Object.fromEntries(CATEGORIES.map((c) => [c, by((f) => f.category)[c] ?? 0])),
    by_section: base.by_section.map((s) => ({ ...s, count: bySection[s.key] ?? 0 })),
  };
}

/** 解析の開始時に使ったルール（承認済みの件数と最終更新日時） */
function rulesetText(settings: AnalysisSummary["analysis"]["settings"]): string {
  const ruleset = (settings as { ruleset?: { approved: number; updated_at: string | null } }).ruleset;
  if (!ruleset) return "記録なし";
  const when = ruleset.updated_at ? `、${new Date(ruleset.updated_at).toLocaleString("ja-JP")} 更新` : "";
  return `承認済み ${ruleset.approved}件${when}`;
}

export function DashboardPage({ analysisId }: { analysisId: string }) {
  const [summary, setSummary] = useState<AnalysisSummary | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [error, setError] = useState<string | null>(null);
  const [diff, setDiff] = useState<AnalysisDiff | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, f] = await Promise.all([api.summary(analysisId), api.findings(analysisId)]);
      setSummary(s);
      setFindings(f);
    } catch (e) {
      setError(e instanceof Error ? e.message : "読み込めませんでした");
    }
  }, [analysisId]);

  useEffect(() => {
    void load();
  }, [load]);

  const running = summary?.analysis.status === "queued" || summary?.analysis.status === "running";
  // 前回の解析との差分は、解析が終わってから1回だけ取る
  const completed = summary?.analysis.status === "completed";
  useEffect(() => {
    if (!completed) return;
    api.diff(analysisId).then(setDiff, () => setDiff(null));
  }, [completed, analysisId]);
  const newIds = useMemo(() => (diff?.previous ? new Set(diff.new) : undefined), [diff]);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, load]);

  const filtered = useMemo(
    () => (summary ? applyFilters(findings, filters, summary.counts.section_of) : []),
    [findings, filters, summary],
  );
  const counts = useMemo(() => (summary ? countOf(filtered, summary.counts) : null), [filtered, summary]);

  if (error) return <p role="alert" className="error page">{error}</p>;
  if (!summary || !counts) return <p className="page">読み込み中…</p>;

  const filtering = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS);
  const previous = !filtering ? summary.previous : null;
  const title = summary.document?.title ?? summary.paper?.filename ?? summary.analysis.doc_id;
  const categoryData = CATEGORIES.map((c) => ({ label: label(CATEGORY_LABELS, c), value: counts.by_category[c] ?? 0 }))
    .filter((d) => d.value > 0)
    .sort((a, b) => b.value - a.value);
  const sectionData = counts.by_section.map((s) => ({ label: s.label, value: s.count }));
  const set = (key: keyof Filters) => (e: React.ChangeEvent<HTMLSelectElement>) =>
    setFilters({ ...filters, [key]: e.target.value });

  return (
    <div className="page dashboard">
      <div className="dashboard-head">
        <div>
          <p className="eyebrow">論理・読みやすさの指摘（{summary.analysis.status === "completed" ? "解析完了" : summary.analysis.status === "failed" ? "解析失敗" : "解析中"}）</p>
          <h1>{title}</h1>
        </div>
        <div className="downloads">
          <Link className="button-secondary" href={`/analyses/${analysisId}/annotated`}>
            注釈付きPDF
          </Link>
          <a className="button-secondary" href={api.csvUrl(analysisId)}>
            指摘の一覧（CSV）
          </a>
          <a className="button-secondary" href={api.jsonUrl(analysisId)}>
            指摘の一覧（JSON）
          </a>
          <a className="button-secondary" href={api.reportUrl(analysisId)}>
            要約レポート（テキスト）
          </a>
        </div>
      </div>

      <form className="filters" aria-label="絞り込み" onSubmit={(e) => e.preventDefault()}>
        <label>
          カテゴリ
          <select value={filters.category} onChange={set("category")}>
            <option value="">すべて</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {label(CATEGORY_LABELS, c)}
              </option>
            ))}
          </select>
        </label>
        <label>
          重大度
          <select value={filters.severity} onChange={set("severity")}>
            <option value="">すべて</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {label(SEVERITY_LABELS, s)}
              </option>
            ))}
          </select>
        </label>
        <label>
          セクション
          <select value={filters.section} onChange={set("section")}>
            <option value="">すべて</option>
            {summary.counts.by_section.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          確度
          <select value={filters.confidence} onChange={set("confidence")}>
            <option value="">すべて</option>
            <option value="high">高</option>
            <option value="low">低</option>
          </select>
        </label>
        <label>
          ステータス
          <select value={filters.status} onChange={set("status")}>
            <option value="">却下以外すべて</option>
            {Object.entries(STATUS_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        {filtering && (
          <button type="button" className="button-secondary" onClick={() => setFilters(NO_FILTERS)}>
            絞り込みを解除
          </button>
        )}
      </form>

      <div className="grid">
        <section className="panel kpi" aria-labelledby="kpi-title">
          <h2 id="kpi-title" className="panel-title">
            総指摘数{filtering ? "（絞り込み後）" : ""}
          </h2>
          <p className="kpi-value">
            {counts.total}
            <span className="kpi-unit">件</span>
          </p>
          {previous && <p className="kpi-diff">{diffText(counts.total, previous.total)}</p>}
          <dl className="kpi-breakdown">
            {SEVERITIES.map((s) => (
              <div key={s}>
                <dt>{label(SEVERITY_LABELS, s)}</dt>
                <dd>
                  {counts.by_severity[s] ?? 0}件
                  {previous && <span className="sub"> {diffText(counts.by_severity[s] ?? 0, previous.by_severity[s] ?? 0)}</span>}
                </dd>
              </div>
            ))}
          </dl>
        </section>

        {running ? (
          <ProgressPanel analysis={summary.analysis} />
        ) : (
          <section className="panel summary-text" aria-labelledby="summary-title">
            <h2 id="summary-title" className="panel-title">
              要約
            </h2>
            <p>{summaryText(counts, previous?.total)}</p>
            {summary.analysis.status === "failed" && (
              <p role="alert" className="error">
                {summary.analysis.error ?? "解析に失敗しました"}
              </p>
            )}
          </section>
        )}

        <section className="panel half">
          <BarChart
            title="カテゴリ別の指摘件数（今回の解析）"
            unit="件"
            horizontal
            data={categoryData}
            description={categoryData.map((d) => `${d.label} ${d.value}件`).join("、") || "指摘はありません"}
            csvName={`${analysisId}_category`}
          />
        </section>
        <section className="panel half">
          <BarChart
            title="セクション別の指摘件数（論文内の順）"
            unit="件"
            data={sectionData}
            description={sectionData.map((d) => `${d.label} ${d.value}件`).join("、") || "セクションがありません"}
            csvName={`${analysisId}_section`}
          />
        </section>

        {diff?.previous && (
          <section className="panel full" aria-labelledby="diff-title">
            <h2 id="diff-title" className="panel-title">
              前回の解析との差分（前回：{new Date(diff.previous.created_at).toLocaleString("ja-JP")}）
            </h2>
            <dl className="kpi-breakdown">
              <div>
                <dt>解消</dt>
                <dd>{diff.resolved.length}件</dd>
              </div>
              <div>
                <dt>新規</dt>
                <dd>{diff.new.length}件</dd>
              </div>
              <div>
                <dt>継続</dt>
                <dd>{diff.persisting.length}件</dd>
              </div>
            </dl>
            {diff.resolved.length > 0 && (
              <details>
                <summary>解消された指摘（{diff.resolved.length}件）を見る</summary>
                <ul className="resolved-list">
                  {diff.resolved.map((f) => (
                    <li key={f.finding_id}>
                      {label(SUBCATEGORY_LABELS, f.subcategory)}：「{f.quote}」
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </section>
        )}

        <section className="panel full" aria-labelledby="list-title">
          <h2 id="list-title" className="panel-title">
            指摘の一覧（重大度の順、{filtered.length}件）
          </h2>
          <FindingsTable analysisId={analysisId} findings={filtered} newIds={newIds} />
        </section>
      </div>

      <footer className="meta" aria-label="メタ情報">
        <dl>
          <div>
            <dt>解析日時</dt>
            <dd>{new Date(summary.analysis.created_at).toLocaleString("ja-JP")}</dd>
          </div>
          <div>
            <dt>使用モデル</dt>
            <dd>{Object.entries(summary.models).map(([role, model]) => `${role}: ${model}`).join(" ／ ")}</dd>
          </div>
          <div>
            <dt>ルールセット</dt>
            <dd>
              {rulesetText(summary.analysis.settings)}（<Link href="/rules">ルールの一覧</Link>）
            </dd>
          </div>
          <div>
            <dt>おいとまAPIへのリクエスト</dt>
            <dd>{running ? "解析の完了後に集計します" : `${summary.api_requests}回`}</dd>
          </div>
          <div>
            <dt>学外への通信</dt>
            <dd>
              {summary.external_requests}件（<Link href="/audit">外部通信ログ</Link>）
            </dd>
          </div>
          <div>
            <dt>データの定義</dt>
            <dd>
              <Link href="/definitions">指摘の種類・重大度・確度の定義</Link>
            </dd>
          </div>
        </dl>
      </footer>
    </div>
  );
}
