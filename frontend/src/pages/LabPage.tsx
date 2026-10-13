/**
 * 研究室知識の画面（SPEC 3.1〜3.3）：過去の査読コメント・回答書・添削メモの登録と、取り出した過去の指摘の確認。
 * 過去の指摘は、解析のときに似た箇所の検出に使う。資料から取り出した研究室のルールは「ルール」の画面で承認する。
 */

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, type LabComment, type LabDocument } from "../lib/api";
import { LAB_CATEGORY_LABELS, label } from "../lib/labels";
import { Link } from "../lib/router";

const DOCUMENT_STATUS: Record<string, string> = {
  queued: "順番待ち",
  running: "取り込み中",
  completed: "完了",
  failed: "失敗",
};
const ACTIVE = new Set(["queued", "running"]);
const REFRESH_MS = 5_000;

export function LabPage() {
  const [documents, setDocuments] = useState<LabDocument[]>([]);
  const [comments, setComments] = useState<LabComment[] | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    api.labDocuments().then(setDocuments, () => setMessage("資料の一覧を読み込めませんでした"));
    api.labComments().then(setComments, () => setComments([]));
  }, []);
  useEffect(load, [load]);
  const importing = documents.some((d) => ACTIVE.has(d.status));
  useEffect(() => {
    if (!importing) return;
    const timer = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [importing, load]);

  const upload = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setMessage(null);
    try {
      const doc = await api.uploadLabDocument(file);
      setMessage(`「${doc.filename}」を登録しました。取り込みが終わると、過去の指摘が一覧に加わります`);
      setFile(null);
      load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "登録できませんでした");
    } finally {
      setBusy(false);
    }
  };

  const removeDocument = async (doc: LabDocument) => {
    const ok = window.confirm(
      `「${doc.filename}」と、そこから取り出した過去の指摘 ${doc.comment_count} 件・研究室のルール ${doc.rule_count} 件を削除しますか？`,
    );
    if (!ok) return;
    try {
      await api.deleteLabDocument(doc.document_id);
      load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "削除できませんでした");
    }
  };

  const removeComment = async (comment: LabComment) => {
    if (!window.confirm("この過去の指摘を削除しますか？（解析で使われなくなります）")) return;
    try {
      await api.deleteLabComment(comment.comment_id);
      setComments((list) => (list ?? []).filter((c) => c.comment_id !== comment.comment_id));
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "削除できませんでした");
    }
  };

  return (
    <div className="page">
      <h1>研究室知識</h1>
      <div className="grid">
        <section className="panel kpi" aria-labelledby="lab-count-title">
          <h2 id="lab-count-title" className="panel-title">
            解析に使う過去の指摘
          </h2>
          <p className="kpi-value">
            {comments?.length ?? 0}
            <span className="kpi-unit">件</span>
          </p>
          <p className="note">登録した資料：{documents.length}件</p>
        </section>
        <section className="panel summary-text" aria-labelledby="lab-upload-title">
          <h2 id="lab-upload-title" className="panel-title">
            資料を登録する
          </h2>
          <p className="note">
            過去の査読コメントと回答書、指導教員の添削メモなど（PDF・Word・Markdown・テキスト）。
            資料から「過去の指摘」を取り出し、解析のときに似た箇所を「以前この種の箇所で指摘された」として示します。
            資料から読み取れる書き方の決まりは、研究室のルールとして<Link href="/rules">ルール</Link>
            の画面に「未確認」で加わります。資料は学外に送信されず、この研究室の解析だけで使われます。
          </p>
          <form onSubmit={upload} className="upload-form">
            <label>
              研究室知識の資料
              <input
                type="file"
                accept=".pdf,.docx,.md,.markdown,.txt"
                required
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
            <button type="submit" disabled={!file || busy}>
              {busy ? "登録中…" : "登録して取り込む"}
            </button>
          </form>
        </section>
      </div>
      {message && <p aria-live="polite">{message}</p>}

      {documents.length > 0 && (
        <section className="panel" aria-labelledby="lab-docs-title">
          <h2 id="lab-docs-title" className="panel-title">
            登録した資料
          </h2>
          <div className="table-scroll">
            <table className="data-table responsive">
              <caption className="visually-hidden">登録した研究室知識の資料</caption>
              <thead>
                <tr>
                  <th scope="col">資料</th>
                  <th scope="col">状態</th>
                  <th scope="col" className="num">
                    過去の指摘（件）
                  </th>
                  <th scope="col" className="num">
                    ルール（件）
                  </th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((d) => (
                  <tr key={d.document_id}>
                    <td data-label="資料">{d.filename}</td>
                    <td data-label="状態">
                      {label(DOCUMENT_STATUS, d.status)}
                      {d.error && <span className="sub">（{d.error}）</span>}
                    </td>
                    <td className="num" data-label="過去の指摘（件）">
                      {d.comment_count}
                    </td>
                    <td className="num" data-label="ルール（件）">
                      {d.rule_count}
                    </td>
                    <td data-label="操作">
                      <button
                        type="button"
                        className="button-secondary"
                        disabled={ACTIVE.has(d.status)}
                        onClick={() => void removeDocument(d)}
                      >
                        削除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="panel" aria-labelledby="lab-comments-title">
        <h2 id="lab-comments-title" className="panel-title">
          過去の指摘（{comments?.length ?? 0}件）
        </h2>
        {comments === null ? (
          <p>読み込み中…</p>
        ) : comments.length === 0 ? (
          <p>過去の指摘はまだありません。資料を登録してください。</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table responsive rules-table">
              <caption className="visually-hidden">過去の指摘の一覧</caption>
              <thead>
                <tr>
                  <th scope="col">種類</th>
                  <th scope="col">指摘</th>
                  <th scope="col">指摘された箇所の例</th>
                  <th scope="col">出典</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {comments.map((c) => (
                  <tr key={c.comment_id}>
                    <td data-label="種類">{label(LAB_CATEGORY_LABELS, c.category)}</td>
                    <td className="wide" data-label="指摘">{c.comment}</td>
                    <td data-label="指摘された箇所の例">{c.example ?? "-"}</td>
                    <td data-label="出典">{c.source.page ? `${c.source.doc} p.${c.source.page}` : c.source.doc}</td>
                    <td data-label="操作">
                      <button type="button" className="button-secondary" onClick={() => void removeComment(c)}>
                        削除
                      </button>
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
