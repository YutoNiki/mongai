/**
 * E2E テスト用の /api の差し替え。応答は、デモ用の合成論文（demo/demo_paper_ja.pdf）を実際に解析したときの
 * 応答を保存したもの（e2e/fixtures/）。未発表の論文のデータは含まない。
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Page, Request } from "@playwright/test";

import type { AnalysisListItem, AnalysisSummary, Finding } from "../src/lib/api";

const FIXTURES = resolve(import.meta.dirname, "fixtures");

function fixture<T>(name: string): T {
  return JSON.parse(readFileSync(resolve(FIXTURES, name), "utf-8")) as T;
}

export const summary = fixture<AnalysisSummary>("summary.json");
export const analysisId = summary.analysis.analysis_id;
const initialFindings = fixture<Finding[]>("findings.json");
const list = fixture<AnalysisListItem[]>("analyses.json");
const paper = readFileSync(resolve(FIXTURES, "paper.pdf"));

export interface MockApi {
  findings: Finding[];
  /** PATCH で送られた本文 */
  updates: { findingId: string; body: { status: string; reason: string | null } }[];
  /** /api への要求の URL（パスとクエリ） */
  requests: string[];
}

/** core の GET /findings と同じ絞り込み（指定がなければ却下済みも含めてすべて返す） */
function filtered(findings: Finding[], params: URLSearchParams): Finding[] {
  return findings.filter((f) =>
    (["category", "severity", "section_id", "confidence", "status"] as const).every((key) => {
      const value = params.get(key);
      return !value || f[key] === value;
    }),
  );
}

export async function mockApi(page: Page): Promise<MockApi> {
  const state: MockApi = { findings: structuredClone(initialFindings), updates: [], requests: [] };
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  // pdf.js は Web Worker から PDF を読むので、ページではなくコンテキスト全体で差し替える
  await page.context().route("**/api/**", async (route) => {
    const request: Request = route.request();
    const url = new URL(request.url());
    state.requests.push(`${request.method()} ${url.pathname}${url.search}`);
    const path = url.pathname;
    const base = `/api/analyses/${analysisId}`;

    if (path === "/api/analyses" && request.method() === "GET") return route.fulfill(json(list));
    if (path === "/api/usage/today")
      return route.fulfill(json({ day: "2026-10-13", requests: 120, prompt_tokens: 340000, completion_tokens: 410000 }));
    if (path === base) return route.fulfill(json(summary));
    if (path === `${base}/findings`)
      return route.fulfill(json(filtered(state.findings, url.searchParams)));
    const patch = path.match(new RegExp(`^${base}/findings/([^/]+)$`));
    if (patch && request.method() === "PATCH") {
      const findingId = decodeURIComponent(patch[1] ?? "");
      const body = request.postDataJSON() as { status: Finding["status"]; reason: string | null };
      state.updates.push({ findingId, body });
      const index = state.findings.findIndex((f) => f.finding_id === findingId);
      const current = state.findings[index];
      if (!current) return route.fulfill(json({ detail: "指摘が見つかりません" }, 404));
      const updated: Finding = { ...current, status: body.status, dismiss_reason: body.reason };
      state.findings[index] = updated;
      return route.fulfill(json(updated));
    }
    if (path === `/api/documents/${summary.analysis.doc_id}/paper.pdf` || path === `${base}/annotated.pdf`)
      return route.fulfill({ status: 200, contentType: "application/pdf", body: paper });
    if (path === "/api/audit-log") return route.fulfill(json({ available: true, mode: "standard", entries: [] }));
    if (path === "/api/gateway/queue") return route.fulfill(json([]));
    return route.fulfill(json({ detail: `E2E のモックに無い API：${path}` }, 404));
  });
  return state;
}
