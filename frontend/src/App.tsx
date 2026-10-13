import { Layout } from "./components/Layout";
import { matchRoute, usePath } from "./lib/router";
import { AnnotatedPage } from "./pages/AnnotatedPage";
import { AuditPage } from "./pages/AuditPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DefinitionsPage } from "./pages/DefinitionsPage";
import { DetailPage } from "./pages/DetailPage";
import { HomePage } from "./pages/HomePage";
import { QueuePage } from "./pages/QueuePage";

function Route({ path }: { path: string }) {
  let m = matchRoute("/analyses/:id/findings/:fid", path);
  if (m) return <DetailPage key={m.id} analysisId={m.id ?? ""} findingId={m.fid ?? ""} />;
  m = matchRoute("/analyses/:id/annotated", path);
  if (m) return <AnnotatedPage key={m.id} analysisId={m.id ?? ""} />;
  m = matchRoute("/analyses/:id", path);
  if (m) return <DashboardPage key={m.id} analysisId={m.id ?? ""} />;
  if (path === "/audit") return <AuditPage />;
  if (path === "/queue") return <QueuePage />;
  if (path === "/definitions") return <DefinitionsPage />;
  if (path === "/") return <HomePage />;
  return <p className="page">ページが見つかりません。</p>;
}

export function App() {
  const path = usePath();
  return (
    <Layout>
      <Route path={path} />
    </Layout>
  );
}
