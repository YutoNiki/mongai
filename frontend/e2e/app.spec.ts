/**
 * 画面の E2E テスト（SPEC 7）。主な流れ：トップ → ダッシュボード → 指摘の詳細（PDF）→ ステータスの変更。
 * あわせて、画面が学外（外部 CDN など）に一切通信しないこと（CLAUDE.md 最優先ルール5）を確かめる。
 */

import { expect, test, type Page } from "@playwright/test";

import { analysisId, mockApi, summary } from "./mockApi";

const title = summary.document?.title ?? "";

/** 画面が出した要求のうち、配信元（127.0.0.1:4173）以外へのもの */
function watchExternal(page: Page): string[] {
  const external: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (["data:", "blob:"].includes(url.protocol)) return;
    if (url.host !== "127.0.0.1:4173") external.push(request.url());
  });
  return external;
}

test("トップページに解析の一覧と今日の利用回数が出る", async ({ page }) => {
  await mockApi(page);
  const external = watchExternal(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "論文の投稿前セルフレビュー" })).toBeVisible();
  await expect(page.getByRole("link", { name: title })).toBeVisible();
  await expect(page.getByText("120", { exact: false }).first()).toBeVisible();
  expect(external).toEqual([]);
});

test("ダッシュボード：総指摘数・グラフの表形式・絞り込み・2クリックで PDF の該当箇所へ", async ({ page }) => {
  const api = await mockApi(page);
  const external = watchExternal(page);
  await page.goto("/");
  await page.getByRole("link", { name: title }).click();
  await expect(page).toHaveURL(`/analyses/${analysisId}`);
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();

  // 左上の全体指標（総指摘数）
  const kpi = page.getByRole("region", { name: /^総指摘数/ });
  await expect(kpi.locator(".kpi-value")).toHaveText(`${summary.counts.total}件`);

  // グラフは表でも見られる（代替）
  const firstChart = page.locator("figure").first();
  await firstChart.getByRole("button", { name: "表で見る" }).click();
  await expect(firstChart.getByRole("table")).toBeVisible();
  await firstChart.getByRole("button", { name: "グラフで見る" }).click();

  // 絞り込み：論理のカテゴリだけにすると、一覧の行が論理の指摘だけになる
  const logicCount = summary.counts.by_category["logic"] ?? 0;
  await page.getByLabel("カテゴリ").selectOption("logic");
  const rows = page.getByRole("table", { name: "指摘の一覧（重大度の順）" }).locator("tbody tr");
  await expect(rows).toHaveCount(logicCount);
  await page.getByRole("button", { name: "絞り込みを解除" }).click();
  await expect(rows).toHaveCount(summary.counts.total);

  // 指摘の番号 → 詳細画面（PDF ビューア）
  const firstLink = rows.first().getByRole("link");
  const findingId = (await firstLink.textContent()) ?? "";
  await firstLink.click();
  await expect(page).toHaveURL(`/analyses/${analysisId}/findings/${findingId}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(findingId);
  await expect(page.locator("canvas").first()).toBeVisible();

  await expect.poll(() => api.requests.some((r) => r.includes("/paper.pdf"))).toBe(true);
  expect(external).toEqual([]);
});

test("詳細画面：対応済みにする・理由なしでは却下できない・理由を入れて却下する", async ({ page }) => {
  const api = await mockApi(page);
  const finding = api.findings.find((f) => f.status === "open");
  test.skip(!finding, "未対応の指摘がない");
  const id = finding?.finding_id ?? "";
  await page.goto(`/analyses/${analysisId}/findings/${id}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(id);

  await page.getByRole("button", { name: "対応済みにする" }).click();
  await expect(page.getByText("ステータスを更新しました")).toBeVisible();
  expect(api.updates.at(-1)).toEqual({ findingId: id, body: { status: "accepted", reason: null } });

  await page.getByRole("button", { name: "未対応に戻す" }).click();
  await expect.poll(() => api.updates.at(-1)?.body.status).toBe("open");

  const before = api.updates.length;
  await page.getByRole("button", { name: "却下する" }).click();
  await expect(page.getByText("却下する理由を入力してください")).toBeVisible();
  expect(api.updates.length).toBe(before);

  await page.getByLabel(/却下する理由/).fill("意図した表現のため");
  await page.getByRole("button", { name: "却下する" }).click();
  await expect.poll(() => api.updates.at(-1)?.body).toEqual({ status: "dismissed", reason: "意図した表現のため" });
});

test("キーボードだけで本文へ移動できる", async ({ page, isMobile }) => {
  test.skip(isMobile, "キーボード操作はデスクトップで確かめる");
  await mockApi(page);
  await page.goto(`/analyses/${analysisId}`);
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "本文へ移動" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
});

test("外部通信ログの画面が開ける", async ({ page }) => {
  await mockApi(page);
  const external = watchExternal(page);
  await page.goto("/audit");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(external).toEqual([]);
});
