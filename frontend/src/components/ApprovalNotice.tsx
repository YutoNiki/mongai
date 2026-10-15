/**
 * 承認待ちの外部通信の通知（ヘッダーの下の帯。本文を覆わない）。承認キューを定期的に確かめ、新しい承認待ちが出たら知らせる。
 * 承認キューはログインした利用者ごとなので、ゲストでは問い合わせない。
 * 一度閉じた要求は、このタブでは再び知らせない（sessionStorage。使えない環境でも表示は動く）。
 */

import { useCallback, useEffect, useState } from "react";

import { api, type QueueItem } from "../lib/api";
import { useAuth } from "../lib/auth";
import { describeRequest } from "../lib/requests";
import { Link, usePath } from "../lib/router";

const POLL_MS = 15_000;
const STORAGE_KEY = "mongai-notified-requests";
const SHOWN = 3;

function loadSeen(): Set<string> {
  try {
    return new Set(JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

function saveSeen(seen: Set<string>): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify([...seen]));
  } catch {
    // 保存できなくても通知は出せる（同じ要求をもう一度知らせることがあるだけ）
  }
}

export function ApprovalNotice() {
  const path = usePath();
  const { state: auth } = useAuth();
  const signedIn = auth.kind === "user";
  const [items, setItems] = useState<QueueItem[]>([]);
  const [seen, setSeen] = useState<Set<string>>(loadSeen);

  const check = useCallback(() => {
    api.queue().then(
      (queue) => setItems(queue.filter((q) => q.status === "pending")),
      () => setItems([]), // gateway に繋がらないときは知らせない
    );
  }, []);
  useEffect(() => {
    if (!signedIn) {
      setItems([]);
      return;
    }
    check();
    const timer = window.setInterval(check, POLL_MS);
    return () => window.clearInterval(timer);
  }, [check, path, signedIn]);

  const fresh = items.filter((q) => !seen.has(q.request_id));
  const dismiss = () => {
    const next = new Set([...seen, ...items.map((q) => q.request_id)]);
    saveSeen(next);
    setSeen(next);
  };

  // 承認キューの画面では、一覧そのものが見えているので知らせない
  if (fresh.length === 0 || path === "/queue") return null;
  return (
    <section className="approval-notice" role="status" aria-live="polite" aria-labelledby="approval-notice-title">
      <div className="approval-notice-body">
        <h2 id="approval-notice-title" className="approval-notice-title">
          承認待ちの外部通信が{fresh.length}件あります
        </h2>
        <p className="note">承認するまで、学外には送信されません。</p>
        <ul>
          {fresh.slice(0, SHOWN).map((q) => (
            <li key={q.request_id}>{describeRequest(q.request)}</li>
          ))}
          {fresh.length > SHOWN && <li>ほか{fresh.length - SHOWN}件</li>}
        </ul>
      </div>
      <div className="approval-notice-actions">
        <Link href="/queue" className="button-primary" onClick={dismiss}>
          承認キューを開く
        </Link>
        <button type="button" className="button-secondary" onClick={dismiss}>
          あとで
        </button>
      </div>
    </section>
  );
}
