import { useCallback, useEffect, useState, type FormEvent, type MouseEvent } from "react";

import { ProgressBar } from "../components/ProgressBar";
import { InstructionComposer } from "../components/InstructionComposer";
import { api, type AnalysisListItem, type DailyUsage } from "../lib/api";
import { loginUrl, useAuth } from "../lib/auth";
import { elapsedText } from "../lib/progress";
import { prefetchDashboard } from "../lib/pages";
import { prefetchAnalysis, waitForAnalysis } from "../lib/prefetch";
import { Link, navigate } from "../lib/router";
import { DOCUMENT_LABELS, type DocumentType, type InstructionSelection } from "../lib/instructions";

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
  const { state: auth } = useAuth();
  const signedIn = auth.kind === "user";
  // トップページが表示されたあと、手が空いたときに解析結果の画面の部品（グラフのライブラリ）を読み込んでおく
  useEffect(() => {
    const timer = window.setTimeout(prefetchDashboard, 1500);
    return () => window.clearTimeout(timer);
  }, []);
  const [items, setItems] = useState<AnalysisListItem[] | null>(null);
  // undefined は読み込み中、null は読み込めなかったとき
  const [usage, setUsage] = useState<DailyUsage | null | undefined>(undefined);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [outputLang, setOutputLang] = useState<"paper" | "ja">("paper");
  const [documentType, setDocumentType] = useState<DocumentType>("paper");
  const [instructions, setInstructions] = useState<InstructionSelection>({ ready: true });
  const [busy, setBusy] = useState(false);
  // 送信後にファイルの選択を空にするため、フォームを作り直す
  const [formKey, setFormKey] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listAnalyses().then(setItems, () => setError("解析の一覧を読み込めませんでした"));
    // 利用回数は利用者ごと（ゲストは 401 になるので問い合わせない）
    if (signedIn) api.usageToday().then(setUsage, () => setUsage(null));
    else setUsage(undefined);
  }, [signedIn]);
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
      `「${name}」を削除しますか？\n\n文書のPDFと、この文書の解析結果 ${count} 件（指摘・対応状況を含む）をすべて削除します。元に戻せません。`,
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
    if (files.length === 0 || !instructions.ready) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    const started: string[] = [];
    const failed: string[] = [];
    for (const [index, file] of files.entries()) {
      setNotice(`アップロード中…（${index + 1} / ${files.length}）${file.name}`);
      try {
        const uploaded = await api.upload(file);
        const summary = await api.startAnalysis(uploaded.doc_id, outputLang, {
          document_type: documentType,
          instruction_draft_id: instructions.instruction_draft_id,
          instruction_revision: instructions.instruction_revision,
        });
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
    if (started.length > 0) setNotice(`${started.length}件の文書の解析を始めました。下の一覧で進み具合を確認できます`);
    if (failed.length > 0) setError(`解析を始められなかった文書があります：\n${failed.join("\n")}`);
    load();
  };

  const demo = items?.find((i) => i.sample && i.status === "completed") ?? items?.find((i) => i.sample) ?? null;
  const guest = auth.kind !== "user" && auth.kind !== "loading";

  const list = (
    <section className="panel home-list" aria-labelledby="list-title">
      <h2 id="list-title" className="panel-title">
        解析済みの文書
      </h2>
      {items === null ? (
        <p>読み込み中…</p>
      ) : items.length === 0 ? (
        <p>{signedIn ? "まだ解析はありません。" : "閲覧できる解析はありません。"}</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table responsive">
            <thead>
              <tr>
                <th scope="col">文書</th>
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
                  <th scope="row" data-label="文書">
                    <Link
                      href={`/analyses/${item.analysis_id}`}
                      className="doc-title"
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
  );
  const errorMessage = error && (
    <p role="alert" className="error">
      {error}
    </p>
  );

  // ログインの状態が分かるまでは、ゲスト向け・ログイン中向けのどちらの画面も出さない（表示が入れ替わってずれないように）
  if (auth.kind === "loading") {
    return (
      <div className="page home-page" aria-busy="true">
        <p className="note">読み込み中…</p>
      </div>
    );
  }

  // ゲスト（期限切れ・認証サービスに接続できないときも）：目的 → デモ → ログインの案内 → 一覧
  if (guest) {
    return (
      <div className="page home-page">
        <div className="home-intro">
          <section className="home-hero" aria-labelledby="home-title">
            <h1 id="home-title">論文・レポートの提出前セルフレビュー</h1>
            <p className="home-lead">
              PDFをアップロードすると、学内のAI（おいとま）が論理の飛躍・読みにくさ・書き方のルール違反・引用の誤りを指摘し、改善案を示します。文書と解析結果は学外に送りません。
            </p>
            <div className="home-cta">
              {items === null ? (
                <p className="note">デモの解析結果を読み込んでいます…</p>
              ) : demo ? (
                <>
                  <Link
                    href={`/analyses/${demo.analysis_id}`}
                    className="button-primary-link home-cta-button"
                    transition="forward"
                    onPointerEnter={() => void prefetchAnalysis(demo.analysis_id)}
                    onFocus={() => void prefetchAnalysis(demo.analysis_id)}
                    onClick={(event) => openAnalysis(event, demo.analysis_id)}
                  >
                    デモの解析結果を見る
                  </Link>
                  <span className="note">ログインせずに、合成論文の解析結果を閲覧できます</span>
                </>
              ) : (
                <p className="note">いまは閲覧できるデモの解析結果がありません。</p>
              )}
            </div>
          </section>
          <section className="panel home-login" aria-labelledby="login-title">
            <h2 id="login-title" className="panel-title">
              自分の文書を解析するには
            </h2>
            <p>岡山大学のGoogleアカウントでログインすると、論文・レポートをアップロードして解析できます。</p>
            <a className="button-secondary home-login-button" href={loginUrl("/")}>
              ログインして解析する
            </a>
            <p className="note">
              Googleには本人確認（メールアドレスの確認）だけを依頼し、論文や解析結果は送りません。
            </p>
          </section>
        </div>
        {errorMessage}
        {list}
      </div>
    );
  }

  // ログインした利用者：解析の操作 → 一覧。今日の利用回数は見出しの横に小さく
  return (
    <div className="page home-page">
      <div className="home-head">
        <h1>ダッシュボード</h1>
        <section className="home-usage" aria-labelledby="usage-title">
          <h2 id="usage-title" className="home-usage-title">
            今日のAPIリクエスト
          </h2>
          {usage === undefined ? (
            <p className="note">読み込み中…</p>
          ) : usage === null ? (
            <p>読み込めませんでした</p>
          ) : (
            <p>
              <strong>{number(usage.requests)}回</strong>
              <span className="note">
                （入力 {number(usage.prompt_tokens)}・出力 {number(usage.completion_tokens)} トークン）
              </span>
            </p>
          )}
        </section>
      </div>
      <section className="panel home-upload" aria-labelledby="upload-title">
        <h2 id="upload-title" className="panel-title">
          論文・レポートを解析する
        </h2>
        <p className="note">アップロードした文書と提出要件は、学内のサーバーで保存・解析します。</p>
        <form key={formKey} onSubmit={submit} className="upload-form">
          <div className="upload-fields">
            <label>
              文書の種類
              <select value={documentType} disabled={busy} onChange={(e) => {
                setDocumentType(e.target.value as DocumentType); setInstructions({ ready: true });
              }}>
                {Object.entries(DOCUMENT_LABELS).map(([key, text]) => <option key={key} value={key}>{text}</option>)}
              </select>
            </label>
            <label>
              文書のPDF（複数選べます）
              <input
                type="file"
                disabled={busy}
                accept="application/pdf,.pdf"
                multiple
                required
                onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              />
            </label>
            <label>
              指摘を書く言語
              <select value={outputLang} disabled={busy} onChange={(e) => setOutputLang(e.target.value as "paper" | "ja")}>
                <option value="paper">文書と同じ言語</option>
                <option value="ja">日本語</option>
              </select>
            </label>
          </div>
          <details className="instruction-options">
            <summary>
              <span className="instruction-options-title">課題・提出要件<span className="instruction-optional">任意</span></span>
              <span className="instruction-options-description">学部・講義の様式や、今回の課題の条件を追加できます。</span>
              <svg className="instruction-options-chevron" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </summary>
            <div className="instruction-options-body">
              <fieldset disabled={busy} className="instruction-inputs"><legend className="visually-hidden">課題・提出要件</legend>
                <InstructionComposer key={`${documentType}-${formKey}`} documentType={documentType} onChange={setInstructions} />
              </fieldset>
            </div>
          </details>
          <div className="upload-actions">
            {!instructions.ready && <p className="note">解析を始める前に、今回適用する確認項目を確定してください。</p>}
            <button type="submit" disabled={files.length === 0 || busy || !instructions.ready}>
              {busy
                ? "アップロード中…"
                : files.length > 1
                  ? `${files.length}件をアップロードして解析を始める`
                  : "アップロードして解析を始める"}
            </button>
          </div>
        </form>
        {notice && (
          <p className="note" aria-live="polite">
            {notice}
          </p>
        )}
      </section>
      {errorMessage}
      {list}
    </div>
  );
}
