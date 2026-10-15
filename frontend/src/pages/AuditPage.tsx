import { useEffect, useState } from "react";

import { AuthChecking, LoginRequired } from "../components/LoginRequired";
import { api, type AuditEntry } from "../lib/api";
import { useAnalysisTitles } from "../lib/analysisTitles";
import { useAuth } from "../lib/auth";
import { DECISION_LABELS, MODE_LABELS, REQUEST_KIND_LABELS, label } from "../lib/labels";
import { describeRequest } from "../lib/requests";
import { Link } from "../lib/router";
import { useDocumentTitle } from "../lib/title";

/** 外部通信ログ（SPEC 2.3）。検索ゲートウェイを通った要求を、拒否・承認待ちも含めてすべて表示する */
export function AuditPage() {
  useDocumentTitle("外部通信ログ");
  const { state: auth } = useAuth();
  const signedIn = auth.kind === "user";
  const titles = useAnalysisTitles(signedIn);
  const [log, setLog] = useState<{ available: boolean; mode: string | null; entries: AuditEntry[] } | null>(null);
  useEffect(() => {
    if (!signedIn) return;
    api.auditLog().then(setLog, () => setLog({ available: false, mode: null, entries: [] }));
  }, [signedIn]);
  // ゲストには API を問い合わせず、ログインの案内だけを出す（API はゲストに 401 を返す）
  if (!signedIn)
    return (
      <div className="page">
        <h1>外部通信ログ</h1>
        {auth.kind === "loading" ? (
          <AuthChecking />
        ) : (
          <LoginRequired path="/audit">
            自分の解析で学外に送った要求（引用文献の書誌情報・承認した用語の照会）の記録を確認できます。
          </LoginRequired>
        )}
      </div>
    );
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
            <table className="data-table responsive">
              <thead>
                <tr>
                  <th scope="col">日時</th>
                  <th scope="col">判定</th>
                  <th scope="col">種類</th>
                  <th scope="col">内容</th>
                  <th scope="col">理由</th>
                  <th scope="col">文書</th>
                </tr>
              </thead>
              <tbody>
                {log.entries.map((e) => (
                  <tr key={e.seq}>
                    <td data-label="日時">{new Date(e.ts).toLocaleString("ja-JP")}</td>
                    <td data-label="判定">{label(DECISION_LABELS, e.decision)}</td>
                    <td data-label="種類">{e.kind ? label(REQUEST_KIND_LABELS, e.kind) : "—"}</td>
                    <td data-label="内容">{describeRequest(e.request)}</td>
                    <td data-label="理由">{e.reason ?? ""}</td>
                    <td data-label="文書">
                      {e.analysis_id ? (
                        <Link href={`/analyses/${e.analysis_id}`} className="doc-title">
                          {titles.get(e.analysis_id) ?? e.analysis_id}
                        </Link>
                      ) : (
                        "—"
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
