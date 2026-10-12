import { useCallback, useEffect, useState } from "react";

import { api, type QueueItem } from "../lib/api";
import { REQUEST_KIND_LABELS, label } from "../lib/labels";
import { describeRequest } from "../lib/requests";
import { Link } from "../lib/router";

/** 承認キュー（SPEC 2.3）。承認した要求だけが学外に送信される */
export function QueuePage() {
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    api.queue().then(setItems, () => setMessage("承認キューを読み込めませんでした"));
  }, []);
  useEffect(load, [load]);

  const decide = async (item: QueueItem, action: "approve" | "reject") => {
    try {
      const r = await api.decide(item.request_id, action);
      setMessage(`${describeRequest(item.request)} を${action === "approve" ? "承認しました（" + r.decision + "）" : "却下しました"}`);
      load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "処理できませんでした");
    }
  };

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
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">種類</th>
                  <th scope="col">内容</th>
                  <th scope="col">解析</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.request_id}>
                    <td>{label(REQUEST_KIND_LABELS, item.kind)}</td>
                    <td>{describeRequest(item.request)}</td>
                    <td>
                      <Link href={`/analyses/${item.analysis_id}`}>{item.analysis_id}</Link>
                    </td>
                    <td className="status-buttons">
                      <button type="button" onClick={() => void decide(item, "approve")}>
                        承認して送信
                      </button>
                      <button type="button" className="button-secondary" onClick={() => void decide(item, "reject")}>
                        却下
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
