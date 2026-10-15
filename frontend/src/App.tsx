import { Suspense } from "react";

import { Layout } from "./components/Layout";
import { AuthProvider } from "./lib/auth";
import { AnnotatedPage, DashboardPage, DetailPage } from "./lib/pages";
import { matchRoute, usePath } from "./lib/router";
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
  return <p className="page">ページが見つかりません。</p>;
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
