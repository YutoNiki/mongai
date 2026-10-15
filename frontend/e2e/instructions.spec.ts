import { expect, test, type Page } from "@playwright/test";

import type { InstructionDraft, InstructionProfile, Requirement, RequirementsResponse } from "../src/lib/instructions";
import { analysisId, mockApi, summary } from "./mockApi";

function requirement(id: string, text: string, extra: Partial<Requirement> = {}): Requirement {
  return { requirement_id: id, text, source: "今回の課題", source_excerpt: text,
    strength: "required", kind: "content", minimum: null, maximum: null, value: null,
    include_spaces: false, count_scope: "body", aggregation: "all", enabled: true, ...extra };
}

async function mockInstructions(page: Page) {
  await mockApi(page);
  const faculty: InstructionProfile = { profile_id: "IP-faculty", name: "学部の共通様式", scope: "faculty",
    raw_text: "である調", revision: 1, updated_at: "2026-10-15T00:00:00Z",
    requirements: [requirement("REQ-style", "である調", { kind: "style", value: "である" })] };
  const other: InstructionProfile = { ...faculty, profile_id: "IP-other", name: "別の講義", scope: "course",
    requirements: [requirement("REQ-other", "別の講義だけの条件")] };
  const state = {
    profiles: [faculty, other],
    draft: null as InstructionDraft | null,
    selected: [] as string[],
    started: [] as { document_type: string; instruction_draft_id: string; instruction_revision: number }[],
    results: null as RequirementsResponse | null,
  };
  await page.context().route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const respond = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/instruction-profiles" && method === "GET") return respond(state.profiles);
    if (path === "/api/instruction-drafts" && method === "POST") {
      const body = request.postDataJSON() as { document_type: InstructionDraft["document_type"]; raw_text: string; profiles: { profile_id: string }[] };
      state.selected = body.profiles.map((p) => p.profile_id);
      const chosen = state.profiles.filter((p) => state.selected.includes(p.profile_id));
      state.draft = { draft_id: "ID-test", document_type: body.document_type, raw_text: body.raw_text,
        profiles: chosen, requirements: [...chosen.flatMap((p) => p.requirements.map((r) => ({ ...r, source: p.name }))),
          ...(body.raw_text ? [requirement("REQ-count", "2000字以内", { kind: "character_count", maximum: 2000 }),
            requirement("REQ-font", "フォント12pt", { kind: "manual" })] : [])],
        conflicts: [], status: body.raw_text ? "queued" : "ready", error: null, revision: 1,
        confirmed: false, legacy_rules: [], excluded_rule_ids: [] };
      return respond(state.draft, 201);
    }
    if (path === "/api/instruction-drafts/ID-test" && method === "GET" && state.draft) {
      state.draft = { ...state.draft, status: "ready" };
      return respond(state.draft);
    }
    if (path === "/api/instruction-drafts/ID-test" && method === "PATCH" && state.draft) {
      const body = request.postDataJSON() as { revision: number; requirements: Requirement[]; excluded_rule_ids: string[] };
      if (body.revision !== state.draft.revision) return respond({ detail: "古い版です" }, 409);
      state.draft = { ...state.draft, ...body, revision: state.draft.revision + 1, confirmed: false };
      return respond(state.draft);
    }
    if (path === "/api/instruction-drafts/ID-test/confirm" && state.draft) {
      state.draft = { ...state.draft, confirmed: true, revision: state.draft.revision + 1 };
      return respond(state.draft);
    }
    if ((path === "/api/instruction-profiles" || path.startsWith("/api/instruction-profiles/")) && (method === "POST" || method === "PATCH") && state.draft) {
      const body = request.postDataJSON() as { name: string; scope: "faculty" | "course"; revision?: number };
      const id = method === "PATCH" ? path.split("/").at(-1) ?? "" : "IP-saved";
      const profile: InstructionProfile = { profile_id: id, name: body.name, scope: body.scope,
        raw_text: state.draft.raw_text, requirements: state.draft.requirements,
        revision: (body.revision ?? 0) + 1, updated_at: "2026-10-15T00:00:00Z" };
      state.profiles = [profile, ...state.profiles.filter((p) => p.profile_id !== id)];
      return respond(profile, method === "POST" ? 201 : 200);
    }
    if (path.startsWith("/api/instruction-profiles/") && method === "DELETE") {
      state.profiles = state.profiles.filter((p) => p.profile_id !== path.split("/").at(-1));
      return route.fulfill({ status: 204 });
    }
    if (path === "/api/analyses" && method === "POST" && state.draft) {
      const body = request.postDataJSON() as (typeof state.started)[number];
      state.started.push(body);
      const snapshot = { schema_version: 1, document_type: state.draft.document_type, profiles: structuredClone(state.draft.profiles),
        raw_text: state.draft.raw_text, requirements: structuredClone(state.draft.requirements), excluded_rule_ids: [] };
      state.results = { snapshot, results: snapshot.requirements.map((r) => ({ requirement_id: r.requirement_id,
        verdict: r.kind === "manual" ? "unknown" : r.kind === "character_count" ? "not_met" : "met",
        reason: r.kind === "manual" ? "この条件は自動判定に未対応です。" : r.kind === "character_count" ? "抽出本文は2500文字です。" : "指定の文体を確認しました。",
        sentence_ids: [], paragraph_ids: [], pages: [], quote: "", coverage: r.kind === "manual" ? "none" : "full",
        finding_id: null, state: "completed", suggestion: "" })) };
      return respond({ ...summary, analysis: { ...summary.analysis, status: "queued", settings: {
        document_type: body.document_type, instruction_snapshot: snapshot,
      } } }, 201);
    }
    if (path === `/api/analyses/${analysisId}` && state.results) return respond({ ...summary,
      analysis: { ...summary.analysis, settings: { document_type: state.results.snapshot?.document_type,
        instruction_snapshot: state.results.snapshot } } });
    if (path === `/api/analyses/${analysisId}/requirements` && state.results) return respond(state.results);
    return route.fallback();
  });
  return state;
}

test("講義の要件を選択・編集・確定し、PDFごとに同じ条件で解析する", async ({ page }, testInfo) => {
  const state = await mockInstructions(page);
  const external: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!["data:", "blob:"].includes(url.protocol) && url.host !== "127.0.0.1:4173") external.push(url.href);
  });
  await page.goto("/");
  await page.getByLabel("文書の種類").selectOption("course_report");
  await page.locator("summary").filter({ hasText: "課題・提出要件" }).click();
  await page.getByLabel("学部共通：学部の共通様式（版1）").check();
  await page.getByLabel("今回の課題・提出要件（任意）").fill("2000字以内。フォント12pt。");
  await page.getByLabel("文書のPDF（複数選べます）").setInputFiles([
    { name: "report1.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n") },
    { name: "report2.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n") },
  ]);
  const submit = page.getByRole("button", { name: "2件をアップロードして解析を始める" });
  await expect(submit).toBeDisabled();
  await page.getByRole("button", { name: "確認項目を作る", exact: true }).click();
  await expect(page.getByRole("heading", { name: "今回適用する確認項目" })).toBeVisible();
  expect(state.selected).toEqual(["IP-faculty"]);
  await expect(page.getByLabel("確認項目2の上限")).toHaveValue("2000");
  await page.getByLabel("確認項目2の上限").fill("1500");
  await expect(page.getByLabel("確認項目2の内容")).toHaveValue(/1500文字以内/);
  await page.getByRole("button", { name: "この確認項目で確定する" }).click();
  await expect(submit).toBeEnabled();
  await page.getByLabel("確認項目2の上限").fill("1400");
  await expect(submit).toBeDisabled();
  await page.getByRole("button", { name: "この確認項目で確定する" }).click();
  await expect(submit).toBeEnabled();
  await page.getByText("この条件を次回も使う", { exact: true }).click();
  await page.getByLabel("設定名", { exact: true }).fill("社会調査論の期末要件");
  await page.getByRole("button", { name: "設定を保存する", exact: true }).click();
  await expect(page.getByText("「社会調査論の期末要件」を保存しました。", { exact: false })).toBeVisible();
  const overflow = await page.locator(".instruction-composer").evaluate((element) => element.scrollWidth - element.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: testInfo.outputPath("requirements-input.png"), fullPage: true });
  const revision = state.draft?.revision;
  await submit.click();
  await expect(page.getByText("2件の文書の解析を始めました。", { exact: false })).toBeVisible();
  expect(state.started).toHaveLength(2);
  expect(state.started.every((s) => s.document_type === "course_report" && s.instruction_revision === revision)).toBe(true);
  await page.goto(`/analyses/${analysisId}`);
  const table = page.getByRole("table", { name: "提出要件ごとの判定" });
  await expect(table.getByRole("row").filter({ hasText: "1400文字以内" })).toContainText("満たさない");
  await expect(table.getByRole("row").filter({ hasText: "フォント12pt" })).toContainText("判断できない");
  await expect(table.getByRole("link", { name: "PDFの該当箇所を見る" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "要件の確認結果（CSV）" })).toHaveAttribute("href", `/api/analyses/${analysisId}/requirements.csv`);
  await page.screenshot({ path: testInfo.outputPath("requirements-results.png"), fullPage: true });
  await page.getByRole("region", { name: "提出要件の確認" }).screenshot({ path: testInfo.outputPath("requirements-panel.png") });
  expect(external).toEqual([]);
});

test("個人用の設定を編集・削除できる", async ({ page }) => {
  const state = await mockInstructions(page);
  await page.goto("/instructions");
  const item = page.getByRole("listitem").filter({ hasText: "別の講義" });
  await item.getByRole("button", { name: "編集", exact: true }).click();
  await page.getByRole("button", { name: "確認項目を作る", exact: true }).click();
  await expect(page.getByLabel("確認項目1の内容")).toHaveValue("別の講義だけの条件");
  await page.getByLabel("確認項目1の内容").fill("問いに対する考察を含める");
  await page.getByRole("button", { name: "この確認項目で確定する" }).click();
  await page.getByText("この条件を次回も使う", { exact: true }).click();
  await page.getByRole("button", { name: "設定を更新する", exact: true }).click();
  await expect(page.getByRole("listitem").filter({ hasText: "別の講義" })).toContainText("版2");
  expect(state.profiles.find((p) => p.profile_id === "IP-other")?.requirements[0]?.text).toBe("問いに対する考察を含める");
  page.on("dialog", (dialog) => void dialog.accept());
  await page.getByRole("listitem").filter({ hasText: "別の講義" }).getByRole("button", { name: "削除", exact: true }).click();
  await expect(page.getByRole("listitem").filter({ hasText: "別の講義" })).toHaveCount(0);
});
