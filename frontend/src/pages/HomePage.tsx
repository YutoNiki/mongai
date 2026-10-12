import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, type AnalysisListItem } from "../lib/api";
import { Link, navigate } from "../lib/router";

const ANALYSIS_STATUS: Record<string, string> = {
  queued: "順番待ち",
  running: "解析中",
  completed: "完了",
  failed: "失敗",
};

export function HomePage() {
  const [items, setItems] = useState<AnalysisListItem[] | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [outputLang, setOutputLang] = useState<"paper" | "ja">("paper");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listAnalyses().then(setItems, () => setError("解析の一覧を読み込めませんでした"));
  }, []);
  useEffect(load, [load]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const uploaded = await api.upload(file);
      const summary = await api.startAnalysis(uploaded.doc_id, outputLang);
      navigate(`/analyses/${summary.analysis.analysis_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "解析を開始できませんでした");
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <h1>論文の投稿前セルフレビュー</h1>
      <section className="panel" aria-labelledby="upload-title">
        <h2 id="upload-title" className="panel-title">
          論文を解析する
        </h2>
        <p className="note">
          アップロードした論文は学内のサーバーにだけ保存され、学外には送信されません。
          実際の未発表論文を扱うときは、開発用のAIツール（Claude Code など）を起動していない状態で操作してください。
        </p>
        <form onSubmit={submit} className="upload-form">
          <label>
            論文のPDF
            <input type="file" accept="application/pdf,.pdf" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <label>
            指摘を書く言語
            <select value={outputLang} onChange={(e) => setOutputLang(e.target.value as "paper" | "ja")}>
              <option value="paper">論文と同じ言語</option>
              <option value="ja">日本語</option>
            </select>
          </label>
          <button type="submit" disabled={!file || busy}>
            {busy ? "アップロード中…" : "アップロードして解析を始める"}
          </button>
        </form>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
      </section>

      <section className="panel" aria-labelledby="list-title">
        <h2 id="list-title" className="panel-title">
          解析の一覧（新しい順）
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
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.analysis_id}>
                    <th scope="row" data-label="論文">
                      <Link href={`/analyses/${item.analysis_id}`}>{item.title ?? item.filename ?? item.doc_id}</Link>
                    </th>
                    <td data-label="状態">{ANALYSIS_STATUS[item.status] ?? item.status}</td>
                    <td className="num" data-label="指摘数">
                      {item.total}件
                    </td>
                    <td data-label="開始日時">{new Date(item.created_at).toLocaleString("ja-JP")}</td>
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
