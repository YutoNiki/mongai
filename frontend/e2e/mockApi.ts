/**
 * E2E テスト用の /api の差し替え。応答は、デモ用の合成論文（demo/demo_paper_ja.pdf）を実際に解析したときの
 * 応答を保存したもの（e2e/fixtures/）。未発表の論文のデータは含まない。
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Page, Request } from "@playwright/test";

import type { AnalysisListItem, AnalysisSummary, Finding, LabComment, QueueItem, Rule, TermLookup } from "../src/lib/api";

const FIXTURES = resolve(import.meta.dirname, "fixtures");

function fixture<T>(name: string): T {
  return JSON.parse(readFileSync(resolve(FIXTURES, name), "utf-8")) as T;
}

export const summary = fixture<AnalysisSummary>("summary.json");
export const analysisId = summary.analysis.analysis_id;
const initialFindings = fixture<Finding[]>("findings.json");
const list = fixture<AnalysisListItem[]>("analyses.json");
const paper = readFileSync(resolve(FIXTURES, "paper.pdf"));

/** ルールの画面用の合成のルール（組み込み1件・抽出した未確認2件） */
function sampleRules(): Rule[] {
  const base = {
    checker: null,
    lang: "both" as const,
    merged_sources: [],
    field_tags: [],
    builtin: false,
    document_id: "RD-000000000001",
    updated_at: "2026-10-13T00:00:00+00:00",
  };
  return [
    {
      ...base,
      rule_id: "B-UNIT",
      target_section: "全体",
      rule: "同じ単位は論文全体で同じ表記にする",
      check_type: "mechanical",
      checker: "unit_consistency",
      source: { doc: "組み込み", page: null },
      status: "approved",
      builtin: true,
      document_id: null,
    },
    {
      ...base,
      rule_id: "R-0001",
      target_section: "Abstract",
      rule: "要旨には目的・方法・結果・結論を含める",
      check_type: "llm",
      source: { doc: "投稿規定.md", page: null },
      status: "pending",
    },
    {
      ...base,
      rule_id: "R-0002",
      target_section: "Discussion",
      rule: "結論では本文に無い新しい主張をしない",
      check_type: "llm",
      source: { doc: "投稿規定.md", page: null },
      status: "pending",
    },
  ];
}

/** 専門知識の画面用の合成の過去の指摘 */
function sampleLabComments(): LabComment[] {
  return [
    {
      comment_id: "LC-0001",
      lab_id: "default",
      document_id: "LD-000000000001",
      comment: "1つのデータセットの結果から、手法の有効性を一般化して述べている",
      example: "提案手法はあらゆる場面で有効である",
      category: "logic",
      source: { doc: "2024年度_査読コメント.docx", page: null },
      created_at: "2026-10-13T00:00:00+00:00",
    },
  ];
}

export interface MockApi {
  labComments: LabComment[];
  rules: Rule[];
  findings: Finding[];
  /** PATCH で送られた本文 */
  updates: { findingId: string; body: { status: string; reason: string | null } }[];
  /** /api への要求の URL（パスとクエリ） */
  requests: string[];
  /** 承認キュー（既定は空。通知のテストで積む） */
  queue: QueueItem[];
  /** この解析で照会した用語 */
  terms: TermLookup[];
  /** アップロードされたファイルの数と、解析を始めた論文の ID */
  uploads: number;
  started: string[];
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

/** sample：解析の集計の `sample`（デモかどうか）を差し替える。省くと保存した応答のまま（デモ） */
export async function mockApi(page: Page, options: { sample?: boolean } = {}): Promise<MockApi> {
  const state: MockApi = {
    labComments: sampleLabComments(),
    rules: sampleRules(),
    findings: structuredClone(initialFindings),
    updates: [],
    requests: [],
    queue: [],
    terms: [],
    uploads: 0,
    started: [],
  };
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
    if (path === base) return route.fulfill(json({ ...summary, sample: options.sample ?? summary.sample }));
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
    if (path === `${base}/diff`) return route.fulfill(json({ previous: null, resolved: [], new: [], persisting: [] }));
    if (path === "/api/rules" && request.method() === "GET") return route.fulfill(json(state.rules));
    if (path === "/api/rule-documents" && request.method() === "GET")
      return route.fulfill(
        json([
          {
            document_id: "RD-000000000001",
            filename: "投稿規定.md",
            kind: "markdown",
            status: "completed",
            rule_count: 2,
            error: null,
            created_at: "2026-10-13T00:00:00+00:00",
            finished_at: "2026-10-13T00:01:00+00:00",
          },
        ]),
      );
    const rulePatch = path.match(/^\/api\/rules\/([^/]+)$/);
    if (rulePatch && request.method() === "PATCH") {
      const ruleId = decodeURIComponent(rulePatch[1] ?? "");
      const update = request.postDataJSON() as Partial<Rule>;
      state.rules = state.rules.map((r) => (r.rule_id === ruleId ? { ...r, ...update } : r));
      return route.fulfill(json(state.rules.find((r) => r.rule_id === ruleId)));
    }
    if (path === "/api/rules/bulk-status") {
      const body = request.postDataJSON() as { rule_ids: string[]; status: Rule["status"] };
      state.rules = state.rules.map((r) => (body.rule_ids.includes(r.rule_id) ? { ...r, status: body.status } : r));
      return route.fulfill(json(state.rules.filter((r) => body.rule_ids.includes(r.rule_id))));
    }
    if (path === "/api/lab/documents" && request.method() === "GET")
      return route.fulfill(
        json([
          {
            document_id: "LD-000000000001",
            lab_id: "default",
            filename: "2024年度_査読コメント.docx",
            kind: "docx",
            status: "completed",
            comment_count: state.labComments.length,
            rule_count: 1,
            error: null,
            created_at: "2026-10-13T00:00:00+00:00",
            finished_at: "2026-10-13T00:01:00+00:00",
          },
        ]),
      );
    if (path === "/api/lab/comments") return route.fulfill(json(state.labComments));
    const labDelete = path.match(/^\/api\/lab\/comments\/([^/]+)$/);
    if (labDelete && request.method() === "DELETE") {
      state.labComments = state.labComments.filter((c) => c.comment_id !== decodeURIComponent(labDelete[1] ?? ""));
      return route.fulfill({ status: 204 });
    }
    if (path === "/api/documents" && request.method() === "POST") {
      state.uploads += 1;
      const docId = `${state.uploads}`.padStart(16, "0");
      return route.fulfill(json({ doc_id: docId, filename: `paper${state.uploads}.pdf`, size: 10 }, 201));
    }
    if (path === "/api/analyses" && request.method() === "POST") {
      const body = request.postDataJSON() as { doc_id: string };
      state.started.push(body.doc_id);
      return route.fulfill(json({ ...summary, analysis: { ...summary.analysis, status: "queued" } }, 201));
    }
    if (path === "/api/gateway/queue") return route.fulfill(json(state.queue));
    if (path === `${base}/terms`) return route.fulfill(json(state.terms));
    if (path === "/api/audit-log") return route.fulfill(json({ available: true, mode: "standard", entries: [] }));
    if (path === "/api/gateway/queue") return route.fulfill(json([]));
    return route.fulfill(json({ detail: `E2E のモックに無い API：${path}` }, 404));
  });
  return state;
}
