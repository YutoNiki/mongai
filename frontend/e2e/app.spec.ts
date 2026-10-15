/**
 * 画面の E2E テスト（SPEC 7）。主な流れ：ダッシュボード（トップ）→ 論文の解析結果 → 指摘の詳細（PDF）→ ステータスの変更。
 * あわせて、画面が学外（外部 CDN など）に一切通信しないこと（CLAUDE.md 最優先ルール5）を確かめる。
 */

import { expect, test, type Page } from "@playwright/test";

import { analysisId, GUEST, list, mockApi, summary } from "./mockApi";

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

/** 狭い画面（メニューが開閉式）では、メニューを開く。広い画面では何もしない */
async function openMenu(page: Page): Promise<void> {
  await expect(page.getByRole("banner")).toBeVisible();
  const toggle = page.getByRole("button", { name: "メニュー", exact: true });
  if ((await toggle.isVisible()) && (await toggle.getAttribute("aria-expanded")) === "false") await toggle.click();
}

test("ログイン中のトップページに、解析のフォーム・解析の一覧・今日の利用回数が出る", async ({ page }) => {
  await mockApi(page);
  const external = watchExternal(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "ダッシュボード" })).toBeVisible();
  await expect(page.getByRole("link", { name: title })).toBeVisible();
  await expect(page.getByRole("region", { name: "今日のAPIリクエスト" })).toContainText("120回");
  await expect(page.getByLabel("文書のPDF（複数選べます）")).toBeVisible();
  // ゲスト向けの案内は出さない
  await expect(page.getByRole("link", { name: "デモの解析結果を見る" })).toHaveCount(0);
  // デモの論文は「（デモ）」と表示し、削除のボタンを出さない
  await expect(page.getByRole("link", { name: `（デモ）${title}` })).toBeVisible();
  await expect(page.getByRole("button", { name: /を削除$/ })).toHaveCount(0);
  expect(external).toEqual([]);
});

test("論文の解析結果：総指摘数・グラフの表形式・絞り込み・2クリックで PDF の該当箇所へ", async ({ page }) => {
  const api = await mockApi(page);
  const external = watchExternal(page);
  await page.goto("/");
  await page.getByRole("link", { name: title }).click();
  await expect(page).toHaveURL(`/analyses/${analysisId}`);
  await expect(page.getByRole("heading", { level: 1, name: `（デモ）${title}` })).toBeVisible();

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
  // スマートフォンでは絞り込みの欄が畳まれているので開く
  const filterToggle = page.getByRole("button", { name: /^絞り込み(（条件あり）)?$/ });
  if (await filterToggle.isVisible()) await filterToggle.click();
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
  const api = await mockApi(page, { sample: false });
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

test("詳細画面：デモの解析では、指摘への対応をこのタブの中だけで変えられる（保存しない）", async ({ page }) => {
  const api = await mockApi(page);
  const finding = api.findings.find((f) => f.status === "open");
  test.skip(!finding, "未対応の指摘がない");
  const id = finding?.finding_id ?? "";
  await page.goto(`/analyses/${analysisId}/findings/${id}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(id);
  await expect(page.getByText(/デモの解析のため、変更はこのタブの中だけに反映され/)).toBeVisible();

  await page.getByRole("button", { name: "対応済みにする" }).click();
  await expect(page.getByText("ステータスを更新しました")).toBeVisible();
  await expect(page.getByRole("button", { name: "未対応に戻す" })).toBeVisible();

  // 再読み込みしても、このタブの中では変えた状態のまま
  await page.reload();
  await expect(page.getByRole("button", { name: "未対応に戻す" })).toBeVisible();
  expect(api.updates).toEqual([]); // サーバーには送らない

  // 注釈付きPDFには、このタブで対応済みにした指摘を書き込まないよう API に伝える
  await page.goto(`/analyses/${analysisId}/annotated`);
  await expect.poll(() => api.requests.some((r) => r.includes(`annotated.pdf?hide=${id}`))).toBe(true);

  // 元に戻すと、保存済みの状態の表示に戻る
  await page.goto(`/analyses/${analysisId}/findings/${id}`);
  await page.getByRole("button", { name: "変更をすべて元に戻す" }).click();
  await expect(page.getByText("このタブでの変更を元に戻しました")).toBeVisible();
  await expect(page.getByRole("button", { name: "未対応に戻す" })).toHaveCount(0);
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
  await openMenu(page);
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

test("専門知識：過去の指摘を確認して削除できる", async ({ page }) => {
  const api = await mockApi(page);
  const external = watchExternal(page);
  await page.goto("/");
  await openMenu(page);
  await page.getByRole("navigation", { name: "メニュー" }).getByRole("link", { name: "専門知識" }).click();
  await expect(page).toHaveURL("/lab");
  await expect(page.getByRole("heading", { level: 1, name: "専門知識" })).toBeVisible();
  await expect(page.getByRole("table", { name: "登録した専門知識の資料" })).toContainText("2024年度_査読コメント.docx");
  const table = page.getByRole("table", { name: "過去の指摘の一覧" });
  await expect(table).toContainText("手法の有効性を一般化して述べている");
  page.once("dialog", (dialog) => void dialog.accept());
  await table.getByRole("button", { name: "削除" }).click();
  await expect(page.getByText("過去の指摘はまだありません。")).toBeVisible();
  expect(api.labComments).toEqual([]);
  expect(external).toEqual([]);
});


test("承認待ちの外部通信をヘッダーの下で知らせ、承認した用語の説明を解析結果に出す", async ({ page }) => {
  const api = await mockApi(page);
  api.queue = [
    {
      request_id: "R-000000000001",
      analysis_id: analysisId,
      doc_id: summary.analysis.doc_id,
      kind: "term_lookup",
      request: { kind: "term_lookup", term: "アイソクローン", lang: "ja" },
      status: "pending",
      created_at: "2026-10-13T12:00:00+00:00",
    },
  ];
  api.terms = [
    {
      term: "アイソクローン",
      query: "isochrone",
      request_id: "R-000000000001",
      status: "pending",
      matched: null,
      description: null,
      source: null,
      reason: null,
      updated_at: "",
    },
    {
      term: "平均絶対誤差",
      query: "mean absolute error",
      request_id: "R-000000000002",
      status: "approved",
      matched: "Forecasting Methods",
      description: "予測値と実測値の差の絶対値の平均",
      source: "openalex",
      reason: null,
      updated_at: "",
    },
  ];
  const external = watchExternal(page);
  await page.goto("/");
  const notice = page.getByRole("status").filter({ hasText: "承認待ちの外部通信が1件あります" });
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("用語「アイソクローン」");
  // 画面に固定せず、本文より上に置く（本文やボタンを覆わない）
  const noticeBottom = await notice.evaluate((el) => el.getBoundingClientRect().bottom);
  const mainTop = await page.locator("main").evaluate((el) => el.getBoundingClientRect().top);
  expect(noticeBottom).toBeLessThanOrEqual(mainTop);
  // 「あとで」で閉じると、同じ要求はもう知らせない
  await notice.getByRole("button", { name: "あとで" }).click();
  await expect(notice).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("承認待ちの外部通信が")).toHaveCount(0);

  await page.goto(`/analyses/${analysisId}`);
  const panel = page.getByRole("region", { name: /未知の用語の照会/ });
  await expect(panel).toContainText("承認待ちが1語あります");
  const approved = panel.getByRole("row", { name: /平均絶対誤差/ });
  await expect(approved).toContainText("照会した語：mean absolute error");
  await expect(approved).toContainText("Forecasting Methods：予測値と実測値の差の絶対値の平均");
  await expect(panel.getByRole("row", { name: /アイソクローン/ })).toContainText("承認すると表示されます");
  expect(external).toEqual([]);
});

test("承認キュー：キューの要求をすべてまとめて承認・却下できる", async ({ page }) => {
  const api = await mockApi(page);
  const item = (n: number, term: string) => ({
    request_id: `R-00000000000${n}`,
    analysis_id: analysisId,
    doc_id: summary.analysis.doc_id,
    kind: "term_lookup",
    request: { kind: "term_lookup" as const, term, lang: "ja" as const },
    status: "pending",
    created_at: "2026-10-13T12:00:00+00:00",
  });
  api.queue = [item(1, "アイソクローン"), item(2, "残差")];
  const external = watchExternal(page);
  await page.goto("/queue");

  // 確認で取り消すと送信しない
  page.once("dialog", (dialog) => void dialog.dismiss());
  await page.getByRole("button", { name: "すべて承認して送信（2 件）" }).click();
  expect(api.decisions).toEqual([]);

  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "すべて承認して送信（2 件）" }).click();
  await expect(page.getByText("2 件を承認しました（送信 2 件）")).toBeVisible();
  await expect(page.getByText("承認待ちの要求はありません。")).toBeVisible();
  expect(api.decisions).toEqual([
    { requestId: "R-000000000001", action: "approve" },
    { requestId: "R-000000000002", action: "approve" },
  ]);

  // 新しく積まれた要求をすべて却下する
  api.queue = [item(3, "交差検証")];
  await page.reload();
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "すべて却下（1 件）" }).click();
  await expect(page.getByText("1 件を却下しました（却下 1 件）")).toBeVisible();
  expect(api.decisions[2]).toEqual({ requestId: "R-000000000003", action: "reject" });
  expect(external).toEqual([]);
});

test("解析結果の左上の「＜」でダッシュボードに戻る（ブラウザの戻るでも戻れる）", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("link", { name: title }).click();
  await expect(page).toHaveURL(`/analyses/${analysisId}`);
  await page.getByRole("link", { name: "ダッシュボードに戻る" }).click();
  await expect(page).toHaveURL("/");
  await expect(page.getByRole("heading", { level: 1, name: "ダッシュボード" })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(`/analyses/${analysisId}`);
  await page.goBack();
  await expect(page).toHaveURL("/");
});

test("論文を複数まとめてアップロードし、それぞれの解析を始める", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/");
  const pdf = { mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n") };
  await page.getByLabel("文書のPDF（複数選べます）").setInputFiles([
    { name: "a.pdf", ...pdf },
    { name: "b.pdf", ...pdf },
    { name: "c.pdf", ...pdf },
  ]);
  await page.getByRole("button", { name: "3件をアップロードして解析を始める" }).click();
  await expect(page.getByText("3件の文書の解析を始めました")).toBeVisible();
  expect(api.uploads).toBe(3);
  expect(api.started).toEqual(["0000000000000001", "0000000000000002", "0000000000000003"]);
  await expect(page).toHaveURL("/"); // 複数のときはトップページに留まる
});

test("ゲスト：共通デモを閲覧でき、論文の解析にはログインを案内する", async ({ page }) => {
  const api = await mockApi(page, { me: GUEST });
  const external = watchExternal(page);
  await page.goto("/");
  const login = page.getByRole("banner").getByRole("link", { name: "Googleでログイン" });
  await expect(login).toHaveAttribute("href", "/auth/login");
  await openMenu(page);
  await expect(page.getByText("ゲスト", { exact: true })).toBeVisible();
  // アップロードの代わりにログインの案内を出す（アクセス制御そのものは API が行う）
  await expect(page.getByLabel("文書のPDF（複数選べます）")).toHaveCount(0);
  const guide = page.getByRole("region", { name: "自分の文書を解析するには" });
  await expect(guide).toContainText("岡山大学のGoogleアカウントでログインすると");
  await expect(guide).toContainText("論文や解析結果は送りません");
  await expect(guide.getByRole("link", { name: "ログインして解析する" })).toHaveAttribute("href", "/auth/login");
  // 利用回数のカードは出さない
  await expect(page.getByRole("region", { name: "今日のAPIリクエスト" })).toHaveCount(0);
  // 利用回数は利用者ごとなので、ゲストでは問い合わせない
  expect(api.requests).not.toContain("GET /api/usage/today");
  // デモの解析結果は開ける
  await page.getByRole("link", { name: `（デモ）${title}` }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(title);
  // ほかの画面から始めると、ログイン後にその画面へ戻る
  await expect(page.getByRole("banner").getByRole("link", { name: "Googleでログイン" })).toHaveAttribute(
    "href",
    `/auth/login?next=${encodeURIComponent(`/analyses/${analysisId}`)}`,
  );
  expect(external).toEqual([]);
});

test("ログイン中：利用者を表示し、ログアウトするとゲストとして読み込み直す", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/");
  await openMenu(page);
  await expect(page.getByText("taro@s.okayama-u.ac.jp")).toBeVisible();
  await expect(page.getByLabel("文書のPDF（複数選べます）")).toBeVisible();
  await page.evaluate(() => window.sessionStorage.setItem("mongai:demo-status:x", "{}"));
  await page.getByRole("button", { name: "ログアウト" }).click();
  await expect(page.getByRole("link", { name: "デモの解析結果を見る" })).toBeVisible();
  await openMenu(page);
  await expect(page.getByText("ゲスト", { exact: true })).toBeVisible();
  expect(api.logouts).toBe(1);
  await expect(page.getByLabel("文書のPDF（複数選べます）")).toHaveCount(0);
  expect(await page.evaluate(() => window.sessionStorage.length)).toBe(0);
});

test("ログインの失敗（許可外のアカウント）を知らせ、URL からエラーを消す", async ({ page }) => {
  await mockApi(page, { me: GUEST });
  await page.goto("/?auth_error=denied");
  await expect(page.getByRole("alert")).toContainText("このGoogleアカウントではログインできません");
  await expect(page).toHaveURL(/\/$/);
  await page.getByRole("button", { name: "閉じる" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("セッションが切れたら、もう一度ログインするかゲストに戻るよう案内する", async ({ page }) => {
  const api = await mockApi(page, { me: "expired" });
  await page.goto("/");
  await expect(page.getByRole("alert").first()).toContainText("ログインの有効期限が切れた");
  await expect(page.getByRole("link", { name: "もう一度ログイン" })).toHaveAttribute("href", "/auth/login");
  await openMenu(page);
  await page.getByRole("button", { name: "ゲストに戻る" }).click();
  // 期限切れの画面もゲスト向けの表示なので、読み込み直して期限切れの知らせが消えるのを待つ
  await expect(page.getByText("ログインの有効期限が切れた", { exact: false })).toHaveCount(0);
  await openMenu(page);
  await expect(page.getByText("ゲスト", { exact: true })).toBeVisible();
  expect(api.logouts).toBe(1);
});

test("ログアウトを確定できなかったときは、ゲストに切り替えずに知らせる", async ({ page }) => {
  const api = await mockApi(page, { logoutStatus: 503 });
  await page.goto("/");
  await openMenu(page);
  await page.getByRole("button", { name: "ログアウト" }).click();
  await expect(page.getByRole("alert")).toContainText("ログアウトを確定できませんでした");
  await expect(page.getByText("taro@s.okayama-u.ac.jp")).toBeVisible();
  expect(api.logouts).toBe(1);
});

// ---- トップページ・ヘッダーの配置（デスクトップ 1600×900・Pixel 7 に加え、360・768・1024px の幅でも確かめる） ----

const MENU_LINKS: [string, string][] = [
  ["ダッシュボード", "/"],
  ["ルール", "/rules"],
  ["課題・提出要件", "/instructions"],
  ["専門知識", "/lab"],
  ["外部通信ログ", "/audit"],
  ["承認キュー", "/queue"],
  ["データの定義", "/definitions"],
];
const FEEDBACK_URL =
  "https://docs.google.com/forms/d/e/1FAIpQLScdFd-8iPWnure9aQZpIleFLHzCrTbfFfuJxCS_HVbqNCVbvw/viewform?usp=dialog";

/** 利用者が削除できる、合成の解析（デモの解析を複製して、別の文書にしたもの） */
function ownAnalysis() {
  const demo = list[0];
  if (!demo) throw new Error("解析の一覧の fixture が空");
  return {
    ...demo,
    analysis_id: "A-000000000001",
    doc_id: "0000000000000abc",
    sample: false,
    title: "とても長い題目の合成論文：".repeat(8) + "改行できない英単語Supercalifragilisticexpialidocious_and_more_text",
  };
}

/** 横スクロールが出ていないか、ヘッダーの要素どうしが重なっていないか */
async function expectNoOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const boxes = await page.evaluate(() =>
    [".site-logo", ".site-header-quick", ".site-nav-wrap", ".account"]
      .map((selector) => document.querySelector(selector))
      .filter((el): el is Element => el !== null && el.getBoundingClientRect().width > 0)
      .map((el) => el.getBoundingClientRect().toJSON() as { left: number; right: number; top: number; bottom: number }),
  );
  for (const [i, a] of boxes.entries())
    for (const b of boxes.slice(i + 1)) {
      const overlap = a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1;
      expect(overlap, `ヘッダーの要素が重なっている：${JSON.stringify([a, b])}`).toBe(false);
    }
}

test("ヘッダー：広い画面ではロゴ・メニュー・ログインの状態を1行に収める", async ({ page, isMobile }) => {
  test.skip(isMobile, "広い画面で確かめる");
  for (const me of [GUEST, undefined]) {
    await mockApi(page, me ? { me } : {});
    for (const width of [1600, 1280, 1121]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      const header = page.getByRole("banner");
      await expect(header.getByRole("link", { name: "ダッシュボード" })).toBeVisible();
      await expect(page.getByRole("button", { name: "メニュー", exact: true })).toBeHidden();
      const height = (await header.boundingBox())?.height ?? 0;
      expect(height, `幅 ${width}px でヘッダーが2段になっている`).toBeLessThanOrEqual(72);
      await expectNoOverflow(page);
    }
  }
});

test("ヘッダー：狭い画面ではメニューを開閉でき、項目を選ぶと閉じる", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "メニュー", exact: true });
  const nav = page.getByRole("navigation", { name: "メニュー" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(nav).toBeHidden();
  // 開く・Esc で閉じてボタンにフォーカスが戻る
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(nav.getByRole("link", { name: "データの定義" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();
  // もう一度開いて、ボタンで閉じる
  await toggle.click();
  await toggle.click();
  await expect(nav).toBeHidden();
  // 項目を選ぶと移動して閉じる
  await toggle.click();
  await nav.getByRole("link", { name: "ルール" }).click();
  await expect(page).toHaveURL("/rules");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(nav).toBeHidden();
});

test("ヘッダー：すべてのメニューの項目に移動でき、今いる画面が分かる", async ({ page }) => {
  await mockApi(page);
  const external = watchExternal(page);
  for (const [name, href] of MENU_LINKS) {
    await page.goto(href === "/" ? "/rules" : "/");
    await openMenu(page);
    const nav = page.getByRole("navigation", { name: "メニュー" });
    const more = nav.getByRole("button", { name: /^その他/ });
    if (["/audit", "/queue", "/definitions"].includes(href) && (await more.isVisible())) {
      await more.click();
      await expect(more).toHaveAttribute("aria-expanded", "true");
    }
    await nav.getByRole("link", { name, exact: true }).click();
    await expect(page).toHaveURL(href);
    await openMenu(page);
    // 「その他」の中の画面にいるときは、閉じた「その他」のボタンでも分かるようにする
    if (["/audit", "/queue", "/definitions"].includes(href) && (await more.isVisible())) {
      await expect(more).toHaveClass(/is-current/);
      await more.click();
    }
    await expect(nav.getByRole("link", { name, exact: true })).toHaveAttribute("aria-current", "page");
  }
  // ご意見・ご要望は Google フォームを新しいタブで開く（このテストではクリックしない）
  await page.goto("/");
  await openMenu(page);
  const nav = page.getByRole("navigation", { name: "メニュー" });
  const more = nav.getByRole("button", { name: /^その他/ });
  if (await more.isVisible()) await more.click();
  const feedback = nav.getByRole("link", { name: /ご意見・ご要望/ });
  await expect(feedback).toBeVisible();
  await expect(feedback).toHaveAttribute("href", FEEDBACK_URL);
  await expect(feedback).toHaveAttribute("target", "_blank");
  await expect(feedback).toHaveAttribute("rel", "noopener noreferrer");
  expect(external).toEqual([]);
});

test("ゲストのトップページ：目的・デモへの導線・ログインの案内の順に出し、デモの解析結果へ進める", async ({ page }) => {
  const api = await mockApi(page, { me: GUEST });
  const external = watchExternal(page);
  await page.goto("/");
  const heading = page.getByRole("heading", { level: 1, name: "論文・レポートの提出前セルフレビュー" });
  const demo = page.getByRole("link", { name: "デモの解析結果を見る" });
  const guide = page.getByRole("region", { name: "自分の文書を解析するには" });
  const listTitle = page.getByRole("heading", { level: 2, name: "解析済みの文書" });
  await expect(heading).toBeVisible();
  await expect(demo).toBeVisible();
  // 画面の上から順に並ぶ（狭い画面では縦一列、広い画面ではデモとログインの案内が横並び）
  const top = async (locator: typeof heading) => (await locator.boundingBox())?.y ?? Number.NaN;
  expect(await top(heading)).toBeLessThan(await top(demo));
  expect(await top(guide)).toBeLessThan(await top(listTitle));
  expect(await top(demo)).toBeLessThan(await top(listTitle));
  // 利用回数は問い合わせず、カードも出さない
  await expect(page.getByRole("region", { name: "今日のAPIリクエスト" })).toHaveCount(0);
  expect(api.requests).not.toContain("GET /api/usage/today");
  // デモの解析 ID は一覧の API から取る
  await expect(demo).toHaveAttribute("href", `/analyses/${analysisId}`);
  await demo.click();
  await expect(page).toHaveURL(`/analyses/${analysisId}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(title);
  expect(external).toEqual([]);
});

test("ゲストのトップページ：デモが無いときは、そのことを知らせる", async ({ page }) => {
  await mockApi(page, { me: GUEST, analyses: [] });
  await page.goto("/");
  await expect(page.getByText("いまは閲覧できるデモの解析結果がありません。")).toBeVisible();
  await expect(page.getByRole("link", { name: "デモの解析結果を見る" })).toHaveCount(0);
  await expect(page.getByText("閲覧できる解析はありません。")).toBeVisible();
});

test("ログイン中のトップページ：自分の文書を一覧から削除できる（デモは削除できない）", async ({ page }) => {
  const own = ownAnalysis();
  const api = await mockApi(page, { analyses: [own, ...list] });
  await page.goto("/");
  const rows = page.getByRole("table").locator("tbody tr");
  await expect(rows).toHaveCount(2);
  await expectNoOverflow(page); // 長い題目でも横にはみ出さない
  const remove = page.getByRole("button", { name: /を削除$/ });
  await expect(remove).toHaveCount(1);
  page.once("dialog", (dialog) => void dialog.accept());
  await remove.click();
  await expect(rows).toHaveCount(1);
  expect(api.deleted).toEqual([own.doc_id]);
  await expect(page.getByRole("link", { name: `（デモ）${title}` })).toBeVisible();
});

for (const width of [360, 768, 1024]) {
  test(`表示幅 ${width}px：横にはみ出さず、ヘッダーはメニューを開閉式にする`, async ({ page, isMobile }) => {
    test.skip(isMobile, "幅を変えて確かめるのはデスクトップの設定で行う");
    for (const me of [GUEST, undefined]) {
      await mockApi(page, me ? { me, analyses: [ownAnalysis(), ...list] } : { analyses: [ownAnalysis(), ...list] });
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      await expect(page.getByRole("heading", { level: 2, name: "解析済みの文書" })).toBeVisible();
      await expect(page.getByRole("button", { name: "メニュー", exact: true })).toBeVisible();
      await expectNoOverflow(page);
      await openMenu(page);
      await expectNoOverflow(page);
      // 押せるものは 44px 以上の高さにする
      for (const link of await page.getByRole("navigation", { name: "メニュー" }).getByRole("link").all())
        expect((await link.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
  });
}

test("スクリーンショット：トップページ（ゲスト・ログイン中）", async ({ page }, testInfo) => {
  for (const [name, me] of [
    ["guest", GUEST],
    ["user", undefined],
  ] as const) {
    await mockApi(page, me ? { me, analyses: [ownAnalysis(), ...list] } : { analyses: [ownAnalysis(), ...list] });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 2, name: "解析済みの文書" })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`home-${name}.png`), fullPage: true });
    const toggle = page.getByRole("button", { name: "メニュー", exact: true });
    if (await toggle.isVisible()) {
      await toggle.click();
      await page.screenshot({ path: testInfo.outputPath(`home-${name}-menu.png`) });
    } else {
      await page.getByRole("button", { name: /^その他/ }).click();
      await page.screenshot({ path: testInfo.outputPath(`home-${name}-more.png`) });
    }
  }
});

test("トップページ：ログインの状態・利用回数の読み込み中に、ほかの画面やエラーを一瞬出さない", async ({ page }) => {
  // GET /api/me と /api/usage/today の応答を、テストから合図するまで止める
  const hold = (pattern: string) => {
    let release = () => {};
    const released = new Promise<void>((resolve) => (release = resolve));
    void page.route(pattern, async (route) => {
      await released;
      await route.fallback();
    });
    return release;
  };

  // ゲスト：確認中はログイン中向けの見出しを出さず、分かったらゲスト向けの画面を出す
  await mockApi(page, { me: GUEST });
  const releaseMe = hold("**/api/me");
  await page.goto("/");
  await expect(page.getByText("読み込み中…")).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(0);
  releaseMe();
  await expect(page.getByRole("heading", { level: 1, name: "論文・レポートの提出前セルフレビュー" })).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });

  // ログイン中：利用回数を読み込んでいる間は「読み込めませんでした」を出さない
  await mockApi(page);
  const releaseUsage = hold("**/api/usage/today");
  await page.goto("/");
  const usage = page.getByRole("region", { name: "今日のAPIリクエスト" });
  await expect(usage).toContainText("読み込み中…");
  await expect(usage).not.toContainText("読み込めませんでした");
  releaseUsage();
  await expect(usage).toContainText("120回");
});

// ---- ほかの画面（ゲストの案内・解析結果のスマートフォン表示・タイルの間隔） ----

test("ゲスト：ログインが必要な画面では、API を問い合わせずにログインを案内する", async ({ page }) => {
  const api = await mockApi(page, { me: GUEST });
  api.queue = [
    {
      request_id: "R-000000000001",
      analysis_id: analysisId,
      doc_id: summary.analysis.doc_id,
      kind: "term_lookup",
      request: { kind: "term_lookup", term: "アイソクローン", lang: "ja" },
      status: "pending",
      created_at: "2026-10-13T12:00:00+00:00",
    },
  ];
  for (const [path, heading] of [
    ["/rules", "ルール"],
    ["/lab", "専門知識"],
    ["/queue", "承認キュー"],
    ["/audit", "外部通信ログ"],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    const guide = page.getByRole("region", { name: "この画面を使うにはログインが必要です" });
    await expect(guide).toBeVisible();
    await expect(guide.getByRole("link", { name: "Googleでログイン" })).toHaveAttribute(
      "href",
      `/auth/login?next=${encodeURIComponent(path)}`,
    );
    // 壊れた画面（アップロードのフォーム・読み込めないという知らせ）を出さない
    await expect(page.locator("input[type=file]")).toHaveCount(0);
    await expect(page.getByText("読み込めませんでした", { exact: false })).toHaveCount(0);
  }
  // ログインが必要な API と、承認待ちの通知（承認キュー）は問い合わせない
  const userOnly = api.requests.filter((r) => /\/api\/(rules|rule-documents|lab\/|gateway\/queue|audit-log)/.test(r));
  expect(userOnly).toEqual([]);
  await expect(page.getByText("承認待ちの外部通信が", { exact: false })).toHaveCount(0);
});

test("解析結果：スマートフォンでは総指摘数を最初の画面に出し、ダウンロード・絞り込みは開いて使う", async ({ page, isMobile }) => {
  test.skip(!isMobile, "スマートフォンの表示で確かめる");
  await mockApi(page);
  await page.goto(`/analyses/${analysisId}`);
  const kpi = page.getByRole("region", { name: /^総指摘数/ });
  await expect(kpi).toBeInViewport();
  const downloads = page.getByRole("button", { name: "注釈付きPDF・ダウンロード" });
  await expect(page.getByRole("link", { name: "指摘の一覧（CSV）" })).toBeHidden();
  await downloads.click();
  await expect(downloads).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("link", { name: "指摘の一覧（CSV）" })).toBeVisible();
  const filters = page.getByRole("button", { name: "絞り込み", exact: true });
  await expect(page.getByLabel("カテゴリ")).toBeHidden();
  await filters.click();
  await page.getByLabel("カテゴリ").selectOption("logic");
  // 条件を付けたことは、欄を畳んでもボタンで分かる
  const filtered = page.getByRole("button", { name: "絞り込み（条件あり）", exact: true });
  await filtered.click();
  await expect(filtered).toHaveAttribute("aria-expanded", "false");
});

test("解析結果：広い画面では、ダウンロードと絞り込みを畳まずに出す", async ({ page, isMobile }) => {
  test.skip(isMobile, "広い画面で確かめる");
  await mockApi(page);
  await page.goto(`/analyses/${analysisId}`);
  await expect(page.getByRole("link", { name: "指摘の一覧（CSV）" })).toBeVisible();
  await expect(page.getByLabel("カテゴリ")).toBeVisible();
  await expect(page.getByRole("button", { name: "注釈付きPDF・ダウンロード" })).toBeHidden();
});

test("ルール・専門知識：上段のタイルと下の欄の間をあける", async ({ page }) => {
  await mockApi(page);
  for (const path of ["/rules", "/lab"]) {
    await page.goto(path);
    const grid = page.locator(".page > .grid");
    await expect(grid).toBeVisible();
    const gap = await grid.evaluate((el) => {
      const next = el.nextElementSibling;
      return next ? next.getBoundingClientRect().top - el.getBoundingClientRect().bottom : Number.NaN;
    });
    expect(gap, `${path} のタイルが下の欄とくっついている`).toBeGreaterThanOrEqual(12);
  }
});

// ---- 全体の UI の確認（折り返し・操作の列・題名・PDF の拡大など） ----

/** 1行に収まっているか（2行以上に折り返すと、行の高さの倍近くになる） */
async function singleLine(locator: ReturnType<Page["locator"]>): Promise<boolean> {
  return locator.evaluate((el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 24;
    return range.getBoundingClientRect().height < lineHeight * 1.5;
  });
}

test("表：短い値やボタンを1文字ずつ折り返さず、操作のボタンを1行に並べる", async ({ page, isMobile }) => {
  test.skip(isMobile, "表の列の幅は広い画面で確かめる");
  await mockApi(page);
  await page.goto("/lab");
  const comments = page.getByRole("table", { name: "過去の指摘の一覧" });
  expect(await singleLine(comments.locator('td[data-label="種類"]').first())).toBe(true);
  expect(await singleLine(comments.getByRole("button", { name: "削除" }))).toBe(true);
  await expect(comments.getByRole("button", { name: "削除" })).toHaveClass(/button-danger/);
  await expect(page.getByRole("table", { name: /登録した専門知識の資料/ }).getByRole("button", { name: "削除" })).toHaveClass(
    /button-danger/,
  );

  await page.goto(`/analyses/${analysisId}`);
  const rows = page.getByRole("table", { name: "指摘の一覧（重大度の順）" }).locator("tbody tr");
  expect(await singleLine(rows.first().getByRole("link").first())).toBe(true);
  expect(await singleLine(rows.first().locator('td[data-label="確度"]'))).toBe(true);

  await page.goto("/rules");
  const pending = page.getByRole("table", { name: "ルールの一覧" }).getByRole("row").filter({ hasText: "要旨には目的" });
  const tops = await Promise.all(
    ["承認", "無効にする", "編集"].map(async (name) => (await pending.getByRole("button", { name }).boundingBox())?.y ?? -1),
  );
  expect(new Set(tops.map(Math.round)).size).toBe(1);
  await expect(page.getByRole("button", { name: "登録してルールを抽出する" })).toHaveCSS("width", /^(?!1\d{3})\d+(\.\d+)?px$/);
});

test("承認キュー：解析 ID ではなく文書の題名を出す", async ({ page }) => {
  const api = await mockApi(page);
  api.queue = [
    {
      request_id: "R-000000000001",
      analysis_id: analysisId,
      doc_id: summary.analysis.doc_id,
      kind: "term_lookup",
      request: { kind: "term_lookup", term: "アイソクローン", lang: "ja" },
      status: "pending",
      created_at: "2026-10-13T12:00:00+00:00",
    },
  ];
  await page.goto("/queue");
  await expect(page.getByRole("link", { name: `（デモ）${title}` })).toHaveAttribute("href", `/analyses/${analysisId}`);
  await expect(page.getByText(analysisId, { exact: true })).toHaveCount(0);
});

test("ブラウザのタブの題名が画面ごとに変わり、存在しない URL では戻り先を示す", async ({ page }) => {
  await mockApi(page);
  for (const [path, name] of [
    ["/", "ダッシュボード"],
    ["/rules", "ルール"],
    ["/lab", "専門知識"],
    ["/queue", "承認キュー"],
    ["/audit", "外部通信ログ"],
    ["/definitions", "データの定義"],
    ["/instructions", "課題・提出要件"],
    [`/analyses/${analysisId}`, `解析結果：${title}`],
    [`/analyses/${analysisId}/annotated`, "注釈付きPDF"],
  ] as const) {
    await page.goto(path);
    await expect(page).toHaveTitle(`${name}｜SI-NSA`);
  }
  await page.goto("/no-such-page");
  await expect(page.getByRole("heading", { level: 1, name: "ページが見つかりません" })).toBeVisible();
  await expect(page).toHaveTitle("ページが見つかりません｜SI-NSA");
  await page.getByRole("link", { name: "ダッシュボードへ戻る" }).click();
  await expect(page).toHaveURL("/");
});

test("ゲスト：課題・提出要件の画面も、ほかの画面と同じくログインを案内する", async ({ page }) => {
  await mockApi(page, { me: GUEST });
  await page.goto("/instructions");
  const guide = page.getByRole("region", { name: "この画面を使うにはログインが必要です" });
  await expect(guide).toBeVisible();
  await expect(guide.getByRole("link", { name: "Googleでログイン" })).toHaveAttribute(
    "href",
    `/auth/login?next=${encodeURIComponent("/instructions")}`,
  );
});

test("PDF：拡大でき、拡大しても画面全体は横にはみ出さない", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/analyses/${analysisId}/annotated`);
  const canvas = page.locator("canvas").first();
  await expect(canvas).toBeVisible();
  await expect.poll(async () => (await canvas.boundingBox())?.width ?? 0).toBeGreaterThan(100);
  const before = (await canvas.boundingBox())?.width ?? 0;
  await expect(page.getByRole("button", { name: "縮小" })).toBeDisabled();
  await page.getByRole("button", { name: "拡大" }).click();
  await expect(page.getByText("150%")).toBeVisible();
  await expect.poll(async () => (await canvas.boundingBox())?.width ?? 0).toBeGreaterThan(before * 1.4);
  const scroll = await page.locator(".pdf-scroll").evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(scroll).toBeGreaterThan(0); // 枠の中で横にスクロールできる
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("スマートフォン：セクション別のグラフは項目名が重ならないよう横棒にし、指摘の一覧を短いカードにする", async ({
  page,
  isMobile,
}) => {
  test.skip(!isMobile, "スマートフォンの表示で確かめる");
  await mockApi(page);
  await page.goto(`/analyses/${analysisId}`);
  const sectionChart = page.locator("figure").filter({ hasText: "セクション別の指摘件数" }).getByRole("img");
  // 横棒は項目の数に応じて高くなる（縦棒は 260px で固定）
  await expect.poll(async () => (await sectionChart.boundingBox())?.height ?? 0).toBeGreaterThan(260);
  const row = page.getByRole("table", { name: "指摘の一覧（重大度の順）" }).locator("tbody tr").first();
  const height = (await row.boundingBox())?.height ?? 0;
  expect(height).toBeLessThan(260);
});

test("スクロールバーの幅を常に空けておき、読み込み後にページが長くなっても本文が左右にずれない", async ({ page }) => {
  // Playwright のブラウザはスクロールバーを表示しないため、ずれそのものではなく設定を確かめる
  await mockApi(page);
  await page.goto("/");
  await expect(page.locator("html")).toHaveCSS("scrollbar-gutter", "stable");
});

// ---- 失敗した解析のやり直し ----

/** 失敗した解析（利用者のもの）を一覧と集計の API に加える。settings は解析を始めたときの設定 */
async function withFailedAnalysis(page: Page, settings: Record<string, unknown>) {
  const demo = list[0];
  if (!demo) throw new Error("解析の一覧の fixture が空");
  const failed = {
    ...demo,
    analysis_id: "A-0000000000f1",
    doc_id: "00000000000000f1",
    sample: false,
    title: "失敗した合成論文",
    status: "failed" as const,
    total: 0,
  };
  const api = await mockApi(page, { analyses: [failed, ...list] });
  // 解析結果の画面が読む付随の API（指摘は0件）
  const sub: Record<string, unknown> = {
    findings: [],
    diff: { previous: null, resolved: [], new: [], persisting: [] },
    terms: [],
    requirements: { snapshot: settings["instruction_snapshot"] ?? null, results: [] },
  };
  await page.route(new RegExp(`/api/analyses/${failed.analysis_id}/(findings|diff|terms|requirements)(\\?.*)?$`), (route) => {
    const key = new URL(route.request().url()).pathname.split("/").at(-1) ?? "";
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(sub[key]) });
  });
  await page.route(`**/api/analyses/${failed.analysis_id}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...summary,
        sample: false,
        analysis: { ...summary.analysis, analysis_id: failed.analysis_id, doc_id: failed.doc_id, status: "failed", error: "PDF の構造化に失敗しました", settings },
      }),
    }),
  );
  return { api, failed };
}

test("失敗した解析：一覧から同じ文書・同じ設定でもう一度解析できる（デモ・完了した解析には出さない）", async ({ page }) => {
  const { api, failed } = await withFailedAnalysis(page, { output_lang: "ja", document_type: "course_report" });
  await page.goto("/");
  const rows = page.getByRole("table").locator("tbody tr");
  await expect(rows.filter({ hasText: "（デモ）" }).getByRole("button", { name: "もう一度解析する" })).toHaveCount(0);
  await rows.filter({ hasText: failed.title }).getByRole("button", { name: "もう一度解析する" }).click();
  await expect(page).toHaveURL(`/analyses/${analysisId}`); // 新しい解析の画面へ
  expect(api.uploads).toBe(0); // アップロードし直さない
  expect(api.startBodies).toEqual([{ doc_id: failed.doc_id, output_lang: "ja", document_type: "course_report" }]);
});

test("失敗した解析：提出要件が変わっていなければ同じ確認項目でやり直し、変わっていれば理由を知らせて止める", async ({ page }) => {
  const requirements = [{ requirement_id: "Q-1", text: "2000字以内" }];
  const snapshot = { schema_version: 1, draft_id: "D-1", document_type: "course_report", profiles: [], raw_text: "", requirements, excluded_rule_ids: [] };
  const { api, failed } = await withFailedAnalysis(page, { output_lang: "paper", document_type: "course_report", instruction_snapshot: snapshot });
  let draftRequirements: unknown = requirements;
  await page.route("**/api/instruction-drafts/D-1", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ draft_id: "D-1", document_type: "course_report", raw_text: "", profiles: [], requirements: draftRequirements, conflicts: [], status: "ready", error: null, revision: 3, confirmed: true, legacy_rules: [], excluded_rule_ids: [] }),
    }),
  );
  // 確認項目が後から変わっている：解析を始めずに理由を出す
  draftRequirements = [{ requirement_id: "Q-1", text: "1500字以内" }];
  await page.goto(`/analyses/${failed.analysis_id}`);
  await page.getByRole("button", { name: "同じ設定でもう一度解析する" }).click();
  await expect(page.getByText("同じ条件ではやり直せません", { exact: false })).toBeVisible();
  expect(api.startBodies).toEqual([]);
  // 変わっていない：同じドラフトの今の版で解析を始める
  draftRequirements = requirements;
  await page.getByRole("button", { name: "同じ設定でもう一度解析する" }).click();
  await expect(page).toHaveURL(`/analyses/${analysisId}`);
  expect(api.startBodies).toEqual([
    { doc_id: failed.doc_id, output_lang: "paper", document_type: "course_report", instruction_draft_id: "D-1", instruction_revision: 3 },
  ]);
});
