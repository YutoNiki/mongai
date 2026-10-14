import { useCallback, useEffect, useState, type FormEvent, type MouseEvent } from "react";

import { ProgressBar } from "../components/ProgressBar";
import { api, type AnalysisListItem, type DailyUsage } from "../lib/api";
import { elapsedText } from "../lib/progress";
import { prefetchDashboard } from "../lib/pages";
import { prefetchAnalysis, waitForAnalysis } from "../lib/prefetch";
import { Link, navigate } from "../lib/router";

const ANALYSIS_STATUS: Record<string, string> = {
  queued: "順番待ち",
  running: "解析中",
  completed: "完了",
  failed: "失敗",
};

const ACTIVE = new Set(["queued", "running"]);
// 「今日の利用」と一覧を読み直す間隔（解析中・順番待ちがあるときは短くする）
const REFRESH_MS = 15_000;
const REFRESH_ACTIVE_MS = 5_000;

/** ゴミ箱のアイコン（外部の素材を使わず、SVG で描く） */
function TrashIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** 経過時間（開始は解析の受け付け時刻なので、順番待ちの時間も含む） */
function ElapsedTime({ item, now }: { item: AnalysisListItem; now: number }) {
  const text = elapsedText(item.created_at, item.finished_at, now);
  if (text === null) return null;
  const prefix = item.status === "queued" ? "待ち時間" : ACTIVE.has(item.status) ? "経過" : "所要時間";
  return (
    <span className="elapsed">
      {prefix} {text}
    </span>
  );
}

const number = (value: number) => value.toLocaleString("ja-JP");

/** 解析結果へ進む：先読みの完了を少し待ってからスライドする（中身のある画面を入れるため） */
function openAnalysis(event: MouseEvent<HTMLAnchorElement>, analysisId: string): void {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
  event.preventDefault();
  void waitForAnalysis(analysisId).then(() => navigate(`/analyses/${analysisId}`, "forward"));
}

export function HomePage() {
  // トップページが表示されたあと、手が空いたときに解析結果の画面の部品（グラフのライブラリ）を読み込んでおく
  useEffect(() => {
    const timer = window.setTimeout(prefetchDashboard, 1500);
    return () => window.clearTimeout(timer);
  }, []);
  const [items, setItems] = useState<AnalysisListItem[] | null>(null);
  const [usage, setUsage] = useState<DailyUsage | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [outputLang, setOutputLang] = useState<"paper" | "ja">("paper");
  const [busy, setBusy] = useState(false);
  // 送信後にファイルの選択を空にするため、フォームを作り直す
  const [formKey, setFormKey] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listAnalyses().then(setItems, () => setError("解析の一覧を読み込めませんでした"));
    api.usageToday().then(setUsage, () => setUsage(null));
  }, []);
  useEffect(load, [load]);
  const active = items?.some((i) => ACTIVE.has(i.status)) ?? false;
  // 経過時間の表示を進めるための時計（解析中だけ動かす）
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, [active]);
  useEffect(() => {
    const timer = window.setInterval(load, active ? REFRESH_ACTIVE_MS : REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load, active]);

  const remove = async (item: AnalysisListItem) => {
    const name = item.title ?? item.filename ?? item.doc_id;
    const count = items?.filter((i) => i.doc_id === item.doc_id).length ?? 1;
    const ok = window.confirm(
      `「${name}」を削除しますか？\n\n論文のPDFと、この論文の解析結果 ${count} 件（指摘・対応状況を含む）をすべて削除します。元に戻せません。`,
    );
    if (!ok) return;
    setDeleting(item.doc_id);
    setError(null);
    try {
      await api.deleteDocument(item.doc_id);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "削除できませんでした");
    } finally {
      setDeleting(null);
    }
  };

  // 複数の論文は1件ずつアップロードして解析を始める（解析はワーカーが順番に、または並行して進める）
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (files.length === 0) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    const started: string[] = [];
    const failed: string[] = [];
    for (const [index, file] of files.entries()) {
      setNotice(`アップロード中…（${index + 1} / ${files.length}）${file.name}`);
      try {
        const uploaded = await api.upload(file);
        const summary = await api.startAnalysis(uploaded.doc_id, outputLang);
        started.push(summary.analysis.analysis_id);
      } catch (e) {
        failed.push(`${file.name}：${e instanceof Error ? e.message : "解析を開始できませんでした"}`);
      }
    }
    setBusy(false);
    setNotice(null);
    // 1件だけなら、これまでどおり解析結果の画面を開く
    if (files.length === 1 && started.length === 1) {
      navigate(`/analyses/${started[0]}`, "forward");
      return;
    }
    setFiles([]);
    setFormKey((k) => k + 1);
    if (started.length > 0) setNotice(`${started.length}件の論文の解析を始めました。下の一覧で進み具合を確認できます`);
    if (failed.length > 0) setError(`解析を始められなかった論文があります：\n${failed.join("\n")}`);
    load();
  };

  return (
    <div className="page">
      <h1>ダッシュボード</h1>
      <div className="grid home-top">
        <section className="panel kpi" aria-labelledby="usage-title">
          <h2 id="usage-title" className="panel-title">
            今日のAPIリクエスト
          </h2>
          {usage === null ? (
            <p>読み込めませんでした</p>
          ) : (
            <>
              <p className="kpi-value">
                {number(usage.requests)}
                <span className="kpi-unit">回</span>
              </p>
              <dl className="kpi-breakdown two">
                <div>
                  <dt>入力トークン</dt>
                  <dd>{number(usage.prompt_tokens)}</dd>
                </div>
                <div>
                  <dt>出力トークン</dt>
                  <dd>{number(usage.completion_tokens)}</dd>
                </div>
              </dl>
            </>
          )}
        </section>
        <section className="panel summary-text" aria-labelledby="upload-title">
          <h2 id="upload-title" className="panel-title">
            論文を解析する
          </h2>
          <p className="note">
            アップロードした論文は学内のサーバーにだけ保存され、学外には送信されません。
          </p>
          <form key={formKey} onSubmit={submit} className="upload-form">
            <label>
              論文のPDF（複数選べます）
              <input
                type="file"
                accept="application/pdf,.pdf"
                multiple
                required
                onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              />
            </label>
            <label>
              指摘を書く言語
              <select value={outputLang} onChange={(e) => setOutputLang(e.target.value as "paper" | "ja")}>
                <option value="paper">論文と同じ言語</option>
                <option value="ja">日本語</option>
              </select>
            </label>
            <button type="submit" disabled={files.length === 0 || busy}>
              {busy
                ? "アップロード中…"
                : files.length > 1
                  ? `${files.length}件をアップロードして解析を始める`
                  : "アップロードして解析を始める"}
            </button>
          </form>
          {notice && (
            <p className="note" aria-live="polite">
              {notice}
            </p>
          )}
        </section>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <section className="panel" aria-labelledby="list-title">
        <h2 id="list-title" className="panel-title">
          解析済みの論文（新しい順。選ぶと解析結果を開きます）
        </h2>
        {items === null ? (
          <p>読み込み中…</p>
        ) : items.length === 0 ? (
          <p>まだ解析はありません。</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table responsive">
              <thead>
                <tr>
                  <th scope="col">論文</th>
                  <th scope="col">状態</th>
                  <th scope="col" className="num">
                    指摘数
                  </th>
                  <th scope="col">開始日時</th>
                  <th scope="col">
                    <span className="visually-hidden">操作</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.analysis_id}>
                    <th scope="row" data-label="論文">
                      <Link
                        href={`/analyses/${item.analysis_id}`}
                        transition="forward"
                        onPointerEnter={() => void prefetchAnalysis(item.analysis_id)}
                        onPointerDown={() => void prefetchAnalysis(item.analysis_id)}
                        onFocus={() => void prefetchAnalysis(item.analysis_id)}
                        onClick={(event) => openAnalysis(event, item.analysis_id)}
                      >
                        {item.sample && "（デモ）"}
                        {item.title ?? item.filename ?? item.doc_id}
                      </Link>
                    </th>
                    <td data-label="状態">
                      {ACTIVE.has(item.status) ? (
                        <ProgressBar status={item.status} progress={item.progress} />
                      ) : (
                        (ANALYSIS_STATUS[item.status] ?? item.status)
                      )}
                      <ElapsedTime item={item} now={now} />
                    </td>
                    <td className="num" data-label="指摘数">
                      {item.total}件
                    </td>
                    <td data-label="開始日時">{new Date(item.created_at).toLocaleString("ja-JP")}</td>
                    <td className="row-actions">
                      {!item.sample && (
                        <button
                          type="button"
                          className="button-danger icon-button"
                          disabled={ACTIVE.has(item.status) || deleting !== null}
                          title={ACTIVE.has(item.status) ? "解析が終わってから削除できます" : "削除"}
                          onClick={() => void remove(item)}
                          aria-label={`「${item.title ?? item.filename ?? item.doc_id}」を削除`}
                          aria-busy={deleting === item.doc_id}
                        >
                          <TrashIcon />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
