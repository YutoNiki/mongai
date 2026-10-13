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

  // 確度と回数は多数決をした指摘にだけ出る（引用の指摘は1回だけの判定なので空欄）
  const confidence = (id: string) => rows.filter({ hasText: id }).locator('td[data-label="確度"]');
  await expect(confidence("F-0005")).toHaveText("高（2回中2回）");
  await expect(confidence("F-0001")).toHaveText("");

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

test("ルール：確認して承認・編集・一括承認できる", async ({ page }) => {
  const api = await mockApi(page);
  const external = watchExternal(page);
  await page.goto("/");
  await page.getByRole("navigation", { name: "メニュー" }).getByRole("link", { name: "ルール" }).click();
  await expect(page).toHaveURL("/rules");
  await expect(page.getByRole("heading", { level: 1, name: "ルール" })).toBeVisible();
  const kpi = page.getByRole("region", { name: /解析に使うルール/ });
  await expect(kpi.locator(".kpi-value")).toHaveText("1件");

  const table = page.getByRole("table", { name: "ルールの一覧" });
  // 組み込みのルールは編集できない
  const builtin = table.getByRole("row").filter({ hasText: "同じ単位は" });
  await expect(builtin.getByRole("button", { name: "編集" })).toHaveCount(0);

  // 1件を編集して承認する
  const first = table.getByRole("row").filter({ hasText: "要旨には目的" });
  await first.getByRole("button", { name: "編集" }).click();
  // 編集中は行の文面がフォームに替わるので、フォームを直接操作する
  const editor = table.locator("form.rule-editor");
  await editor.getByLabel("ルール").fill("要旨には研究の目的・方法・結果・結論を含める");
  await editor.getByRole("button", { name: "保存" }).click();
  await expect(table).toContainText("要旨には研究の目的・方法・結果・結論を含める");
  await table.getByRole("row").filter({ hasText: "要旨には研究の目的" }).getByRole("button", { name: "承認" }).click();
  await expect(kpi.locator(".kpi-value")).toHaveText("2件");

  // 残りの未確認を一括で承認する
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "表示中の未確認をすべて承認" }).click();
  await expect(kpi.locator(".kpi-value")).toHaveText("3件");
  expect(api.rules.every((r) => r.status === "approved")).toBe(true);
  expect(external).toEqual([]);
});

test("研究室知識：過去の指摘を確認して削除できる", async ({ page }) => {
  const api = await mockApi(page);
  const external = watchExternal(page);
  await page.goto("/");
  await page.getByRole("navigation", { name: "メニュー" }).getByRole("link", { name: "研究室知識" }).click();
  await expect(page).toHaveURL("/lab");
  await expect(page.getByRole("heading", { level: 1, name: "研究室知識" })).toBeVisible();
  await expect(page.getByRole("table", { name: "登録した研究室知識の資料" })).toContainText("2024年度_査読コメント.docx");
  const table = page.getByRole("table", { name: "過去の指摘の一覧" });
  await expect(table).toContainText("手法の有効性を一般化して述べている");
  page.once("dialog", (dialog) => void dialog.accept());
  await table.getByRole("button", { name: "削除" }).click();
  await expect(page.getByText("過去の指摘はまだありません。")).toBeVisible();
  expect(api.labComments).toEqual([]);
  expect(external).toEqual([]);
});

