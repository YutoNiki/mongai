import { Suspense } from "react";

import { Layout } from "./components/Layout";
import { AuthProvider } from "./lib/auth";
import { AnnotatedPage, DashboardPage, DetailPage } from "./lib/pages";
import { Link, matchRoute, usePath } from "./lib/router";
import { useDocumentTitle } from "./lib/title";
import { AuditPage } from "./pages/AuditPage";
import { DefinitionsPage } from "./pages/DefinitionsPage";
import { HomePage } from "./pages/HomePage";
import { LabPage } from "./pages/LabPage";
import { QueuePage } from "./pages/QueuePage";
import { RulesPage } from "./pages/RulesPage";
import { InstructionsPage } from "./pages/InstructionsPage";

function Route({ path }: { path: string }) {
  let m = matchRoute("/analyses/:id/findings/:fid", path);
  if (m) return <DetailPage key={m.id} analysisId={m.id ?? ""} findingId={m.fid ?? ""} />;
  m = matchRoute("/analyses/:id/annotated", path);
  if (m) return <AnnotatedPage key={m.id} analysisId={m.id ?? ""} />;
  m = matchRoute("/analyses/:id", path);
  if (m) return <DashboardPage key={m.id} analysisId={m.id ?? ""} />;
  if (path === "/audit") return <AuditPage />;
  if (path === "/queue") return <QueuePage />;
  if (path === "/rules") return <RulesPage />;
  if (path === "/instructions") return <InstructionsPage />;
  if (path === "/lab") return <LabPage />;
  if (path === "/definitions") return <DefinitionsPage />;
  if (path === "/") return <HomePage />;
  return <NotFound />;
}

export function App() {
  const path = usePath();
  return (
    <AuthProvider>
      <Layout>
        <Suspense fallback={<p className="page">読み込み中…</p>}>
          <Route path={path} />
        </Suspense>
      </Layout>
    </AuthProvider>
  );
}

/** 存在しない URL */
function NotFound() {
  useDocumentTitle("ページが見つかりません");
  return (
    <div className="page">
      <h1>ページが見つかりません</h1>
      <section className="panel">
        <p>URL が間違っているか、ページが移動・削除された可能性があります。</p>
        <Link href="/" className="button-primary-link">
          ダッシュボードへ戻る
        </Link>
      </section>
    </div>
  );
}
