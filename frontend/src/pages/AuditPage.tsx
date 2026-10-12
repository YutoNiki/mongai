import { useEffect, useState } from "react";

import { api } from "../lib/api";

/** 外部通信ログ（SPEC 2.3）。学外への要求は M7 の検索ゲートウェイが記録する */
export function AuditPage() {
  const [log, setLog] = useState<{ available: boolean; entries: Record<string, string>[] } | null>(null);
  useEffect(() => {
    api.auditLog().then(setLog, () => setLog({ available: false, entries: [] }));
  }, []);
  return (
    <div className="page">
      <h1>外部通信ログ</h1>
      <section className="panel">
        <p>
          論文の本文・解析結果は学外に送信されません。LLM と埋め込みはすべて学内の おいとまAPI で処理されます。
          学外に出るのは、検索ゲートウェイを通った書誌情報・単独の用語の照会だけで、そのすべてがここに記録されます。
        </p>
        {log === null ? (
          <p>読み込み中…</p>
        ) : log.entries.length === 0 ? (
          <p>学外への通信の記録はありません{log.available ? "" : "（引用の検証は今後のバージョンで有効になります）"}。</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  {Object.keys(log.entries[0] ?? {}).map((k) => (
                    <th key={k} scope="col">
                      {k}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {log.entries.map((e, i) => (
                  <tr key={i}>
                    {Object.values(e).map((v, j) => (
                      <td key={j}>{v}</td>
                    ))}
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
