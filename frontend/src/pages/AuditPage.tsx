import { useEffect, useState } from "react";

import { api, type AuditEntry } from "../lib/api";
import { DECISION_LABELS, MODE_LABELS, REQUEST_KIND_LABELS, label } from "../lib/labels";
import { describeRequest } from "../lib/requests";
import { Link } from "../lib/router";

/** 外部通信ログ（SPEC 2.3）。検索ゲートウェイを通った要求を、拒否・承認待ちも含めてすべて表示する */
export function AuditPage() {
  const [log, setLog] = useState<{ available: boolean; mode: string | null; entries: AuditEntry[] } | null>(null);
  useEffect(() => {
    api.auditLog().then(setLog, () => setLog({ available: false, mode: null, entries: [] }));
  }, []);
  return (
    <div className="page">
      <h1>外部通信ログ</h1>
      <section className="panel">
        <p>
          論文の本文・解析結果は学外に送信されません。LLM と埋め込みはすべて学内の おいとまAPI で処理されます。
          学外に出るのは、検索ゲートウェイの検査を通った引用文献の書誌情報（タイトル・第一著者・年・DOI）と、承認した用語の照会だけです。
        </p>
        {log?.mode && <p>現在の運用モード：{label(MODE_LABELS, log.mode)}</p>}
        <p>
          <Link href="/queue">承認キューを開く</Link>
        </p>
      </section>
      <section className="panel" aria-labelledby="log-title">
        <h2 id="log-title" className="panel-title">
          記録（新しい順）
        </h2>
        {log === null ? (
          <p>読み込み中…</p>
        ) : !log.available ? (
          <p role="alert">検索ゲートウェイに接続できませんでした。</p>
        ) : log.entries.length === 0 ? (
          <p>学外への通信の記録はありません。</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">日時</th>
                  <th scope="col">判定</th>
                  <th scope="col">種類</th>
                  <th scope="col">内容</th>
                  <th scope="col">理由</th>
                  <th scope="col">解析</th>
                </tr>
              </thead>
              <tbody>
                {log.entries.map((e) => (
                  <tr key={e.seq}>
                    <td>{new Date(e.ts).toLocaleString("ja-JP")}</td>
                    <td>{label(DECISION_LABELS, e.decision)}</td>
                    <td>{e.kind ? label(REQUEST_KIND_LABELS, e.kind) : "—"}</td>
                    <td>{describeRequest(e.request)}</td>
                    <td>{e.reason ?? ""}</td>
                    <td>{e.analysis_id ? <Link href={`/analyses/${e.analysis_id}`}>{e.analysis_id}</Link> : "—"}</td>
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
