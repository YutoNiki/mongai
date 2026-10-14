import { useCallback, useEffect, useRef, useState } from "react";

import { api, type QueueItem } from "../lib/api";
import { DECISION_LABELS, REQUEST_KIND_LABELS, label } from "../lib/labels";
import { describeRequest } from "../lib/requests";
import { Link } from "../lib/router";

/** 承認キュー（SPEC 2.3）。承認した要求だけが学外に送信される */
export function QueuePage() {
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // まとめて承認・却下する要求の ID
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const selectAllRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api.queue().then(
      (queue) => {
        setItems(queue);
        // 処理済みで一覧から消えた要求は選択から外す
        const ids = new Set(queue.map((item) => item.request_id));
        setSelected((prev) => new Set([...prev].filter((id) => ids.has(id))));
      },
      () => setMessage("承認キューを読み込めませんでした"),
    );
  }, []);
  useEffect(load, [load]);

  const allSelected = items !== null && items.length > 0 && selected.size === items.length;
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = selected.size > 0 && !allSelected;
  }, [selected, allSelected]);

  const toggle = (requestId: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(requestId)) next.delete(requestId);
      else next.add(requestId);
      return next;
    });
  const toggleAll = () => setSelected(allSelected || !items ? new Set() : new Set(items.map((item) => item.request_id)));

  /** 選択した要求を1件ずつ順に処理する（途中で失敗しても残りを続け、結果を件数でまとめて知らせる） */
  const decideSelected = async (action: "approve" | "reject") => {
    const targets = (items ?? []).filter((item) => selected.has(item.request_id));
    if (targets.length === 0) return;
    if (
      action === "approve" &&
      !window.confirm(
        `選択した ${targets.length} 件を学外に送信します。どれも原稿の内容を含んでいないことを確認しましたか？`,
      )
    ) {
      return;
    }
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
            {/* 表の見出しは狭い画面で隠れるため、「すべて選択」は表の外に置く */}
            <div className="status-buttons">
              <label className="queue-select-all">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  checked={allSelected}
                  disabled={busy}
                  onChange={toggleAll}
                />
                すべて選択
              </label>
              <button type="button" disabled={busy || selected.size === 0} onClick={() => void decideSelected("approve")}>
                選択した {selected.size} 件を承認して送信
              </button>
              <button
                type="button"
                className="button-secondary"
                disabled={busy || selected.size === 0}
                onClick={() => void decideSelected("reject")}
              >
                選択した {selected.size} 件を却下
              </button>
            </div>
            <div className="table-scroll">
              <table className="data-table responsive">
                <thead>
                  <tr>
                    <th scope="col">選択</th>
                    <th scope="col">種類</th>
                    <th scope="col">内容</th>
                    <th scope="col">解析</th>
                    <th scope="col">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.request_id}>
                      <td data-label="選択">
                        <input
                          type="checkbox"
                          aria-label={`${describeRequest(item.request)} を選択`}
                          checked={selected.has(item.request_id)}
                          disabled={busy}
                          onChange={() => toggle(item.request_id)}
                        />
                      </td>
                      <td data-label="種類">{label(REQUEST_KIND_LABELS, item.kind)}</td>
                      <td data-label="内容">{describeRequest(item.request)}</td>
                      <td data-label="解析">
                        <Link href={`/analyses/${item.analysis_id}`}>{item.analysis_id}</Link>
                      </td>
                      <td className="status-buttons" data-label="操作">
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
