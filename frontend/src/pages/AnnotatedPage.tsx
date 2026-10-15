/** 注釈付きPDFをアプリの中で表示する（ダウンロードもできる） */

import { useEffect, useMemo, useState } from "react";

import { CategoryBadge, SeverityBadge } from "../components/Badges";
import { PdfViewer } from "../components/PdfViewer";
import { api, type Finding } from "../lib/api";
import { SUBCATEGORY_LABELS, label } from "../lib/labels";
import { Link } from "../lib/router";
import { useDocumentTitle } from "../lib/title";

export function AnnotatedPage({ analysisId }: { analysisId: string }) {
  useDocumentTitle("注釈付きPDF");
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.findings(analysisId).then(setFindings, (e: unknown) =>
      setError(e instanceof Error ? e.message : "指摘を読み込めませんでした"),
    );
  }, [analysisId]);

  // 注釈付きPDFに書き込まれるのは、却下していない指摘だけ
  const onPage = useMemo(
    () =>
      (findings ?? [])
        .filter((f) => f.status !== "dismissed" && f.page === page)
        .sort((a, b) => (a.bboxes[0]?.y0 ?? 0) - (b.bboxes[0]?.y0 ?? 0)),
    [findings, page],
  );

  return (
    <div className="page annotated">
      <nav aria-label="パンくず" className="breadcrumb">
        <Link href={`/analyses/${analysisId}`} className="button-secondary back-button">
          <span aria-hidden="true">←</span>
          解析結果に戻る
        </Link>
      </nav>
      <div className="annotated-head">
        <h1>注釈付きPDF</h1>
        <a className="button-secondary" href={api.annotatedUrl(analysisId)} download>
          注釈付きPDFをダウンロード
        </a>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="detail-grid">
        <PdfViewer url={api.annotatedUrl(analysisId)} page={page} onPageChange={setPage} />
        <section className="panel" aria-labelledby="page-findings-title">
          <h2 id="page-findings-title" className="panel-title">
            このページの指摘（{findings === null ? "…" : `${onPage.length}件`}）
          </h2>
          {findings !== null && onPage.length === 0 && <p>このページに指摘はありません。</p>}
          <ol className="page-findings">
            {onPage.map((f) => (
              <li key={f.finding_id}>
                <p className="badges">
                  <SeverityBadge severity={f.severity} /> <CategoryBadge category={f.category} />
                </p>
                <Link href={`/analyses/${analysisId}/findings/${f.finding_id}`}>
                  {f.finding_id} {label(SUBCATEGORY_LABELS, f.subcategory)}
                </Link>
                <p>{f.issue}</p>
              </li>
            ))}
          </ol>
          <p className="note">注釈の形と色の意味は「データの定義」にあります。</p>
        </section>
      </div>
    </div>
  );
}
