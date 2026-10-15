import { useCallback, useEffect, useState } from "react";

import { AuthChecking, LoginRequired } from "../components/LoginRequired";
import { api, type QueueItem } from "../lib/api";
import { useAnalysisTitles } from "../lib/analysisTitles";
import { useAuth } from "../lib/auth";
import { DECISION_LABELS, REQUEST_KIND_LABELS, label } from "../lib/labels";
import { describeRequest } from "../lib/requests";
import { Link } from "../lib/router";
import { useDocumentTitle } from "../lib/title";

/** 承認キュー（SPEC 2.3）。承認した要求だけが学外に送信される */
export function QueuePage() {
  useDocumentTitle("承認キュー");
  const { state: auth } = useAuth();
  const signedIn = auth.kind === "user";
  const titles = useAnalysisTitles(signedIn);
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.queue().then(setItems, () => setMessage("承認キューを読み込めませんでした"));
  }, []);
  useEffect(() => {
    if (signedIn) load();
  }, [load, signedIn]);

  /**
   * 表示中の要求をすべて1件ずつ順に処理する（途中で失敗しても残りを続け、結果を件数でまとめて知らせる）。
   * 画面を開いた後にキューに入った要求は、利用者が見ていないので対象にしない
   */
  const decideAll = async (action: "approve" | "reject") => {
    const targets = items ?? [];
    if (targets.length === 0) return;
    const question =
      action === "approve"
        ? `表示中の ${targets.length} 件をすべて学外に送信します。どれも原稿の内容を含んでいないことを確認しましたか？`
        : `表示中の ${targets.length} 件をすべて却下しますか？`;
    if (!window.confirm(question)) return;
    setBusy(true);
    const counts = new Map<string, number>();
    let errors = 0;
    for (const [i, item] of targets.entries()) {
      setMessage(`${action === "approve" ? "承認" : "却下"}しています（${i + 1} / ${targets.length} 件）`);
      try {
        const r = await api.decide(item.request_id, action);
        const key = action === "approve" ? r.decision : "rejected";
        counts.set(key, (counts.get(key) ?? 0) + 1);
      } catch {
        errors += 1;
      }
    }
    const parts = [...counts].map(([decision, n]) => `${label(DECISION_LABELS, decision)} ${n} 件`);
    if (errors > 0) parts.push(`処理できなかったもの ${errors} 件`);
    setMessage(`${targets.length} 件を${action === "approve" ? "承認" : "却下"}しました（${parts.join("、")}）`);
    setBusy(false);
    load();
  };

  const decide = async (item: QueueItem, action: "approve" | "reject") => {
    try {
      const r = await api.decide(item.request_id, action);
      setMessage(`${describeRequest(item.request)} を${action === "approve" ? "承認しました（" + r.decision + "）" : "却下しました"}`);
      load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "処理できませんでした");
    }
  };

  // ゲストには API を問い合わせず、ログインの案内だけを出す（API はゲストに 401 を返す）
  if (!signedIn)
    return (
      <div className="page">
        <h1>承認キュー</h1>
        {auth.kind === "loading" ? (
          <AuthChecking />
        ) : (
          <LoginRequired path="/queue">
            自分の解析で、学外への送信に承認が必要な要求（用語の照会など）を確認し、承認・却下できます。
          </LoginRequired>
        )}
      </div>
    );
  return (
    <div className="page">
      <h1>承認キュー</h1>
      <section className="panel">
        <p>
          学外に送信する前に承認が必要な要求です。内容が原稿の内容を含んでいないことを確認してから承認してください。
          承認した結果は、同じ論文を解析し直すと反映されます。
        </p>
        <p>
          <Link href="/audit">外部通信ログを見る</Link>
        </p>
        {message && <p aria-live="polite">{message}</p>}
      </section>
      <section className="panel" aria-labelledby="queue-title">
        <h2 id="queue-title" className="panel-title">
          承認待ちの要求
        </h2>
        {items === null ? (
          <p>読み込み中…</p>
        ) : items.length === 0 ? (
          <p>承認待ちの要求はありません。</p>
        ) : (
          <>
            <div className="status-buttons">
              <button type="button" disabled={busy} onClick={() => void decideAll("approve")}>
                すべて承認して送信（{items.length} 件）
              </button>
              <button
                type="button"
                className="button-secondary"
                disabled={busy}
                onClick={() => void decideAll("reject")}
              >
                すべて却下（{items.length} 件）
              </button>
            </div>
            <div className="table-scroll">
              <table className="data-table responsive">
                <thead>
                  <tr>
                    <th scope="col">種類</th>
                    <th scope="col">内容</th>
                    <th scope="col">文書</th>
                    <th scope="col">
                      <span className="visually-hidden">操作</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.request_id}>
                      <td data-label="種類">{label(REQUEST_KIND_LABELS, item.kind)}</td>
                      <td data-label="内容">{describeRequest(item.request)}</td>
                      <td data-label="文書">
                        <Link href={`/analyses/${item.analysis_id}`} className="doc-title">
                          {titles.get(item.analysis_id) ?? item.analysis_id}
                        </Link>
                      </td>
                      <td className="cell-actions">
                        <div className="row-buttons">
                        <button type="button" disabled={busy} onClick={() => void decide(item, "approve")}>
                          承認して送信
                        </button>
                        <button
                          type="button"
                          className="button-secondary"
                          disabled={busy}
                          onClick={() => void decide(item, "reject")}
                        >
                          却下
                        </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
