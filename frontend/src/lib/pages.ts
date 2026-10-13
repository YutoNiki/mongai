/**
 * 重いライブラリを使う画面は、開くときに読み込む（コード分割）。
 * 解析結果はグラフ（ECharts）、指摘の詳細・注釈付きPDF は PDF.js を使うので、トップページの読み込みには含めない。
 * prefetch* は、論文の行に触れた時点などで先に読み込み始めるためのもの。
 */

import { lazy } from "react";

const loadDashboard = () => import("../pages/DashboardPage");
const loadDetail = () => import("../pages/DetailPage");
const loadAnnotated = () => import("../pages/AnnotatedPage");

export const DashboardPage = lazy(() => loadDashboard().then((m) => ({ default: m.DashboardPage })));
export const DetailPage = lazy(() => loadDetail().then((m) => ({ default: m.DetailPage })));
export const AnnotatedPage = lazy(() => loadAnnotated().then((m) => ({ default: m.AnnotatedPage })));

export function prefetchDashboard(): void {
  void loadDashboard();
}
