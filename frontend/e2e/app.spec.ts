/**
 * 画面の E2E テスト（SPEC 7）。主な流れ：ダッシュボード（トップ）→ 論文の解析結果 → 指摘の詳細（PDF）→ ステータスの変更。
 * あわせて、画面が学外（外部 CDN など）に一切通信しないこと（CLAUDE.md 最優先ルール5）を確かめる。
 */

import { expect, test, type Page } from "@playwright/test";

import { analysisId, GUEST, mockApi, summary } from "./mockApi";

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
  await expect(page.getByRole("heading", { level: 1, name: "ダッシュボード" })).toBeVisible();
  await expect(page.getByRole("link", { name: title })).toBeVisible();
  await expect(page.getByText("120", { exact: false }).first()).toBeVisible();
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


test("承認待ちの外部通信をポップアップで知らせ、承認した用語の説明を解析結果に出す", async ({ page }) => {
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
  await expect(page.getByText("ゲスト", { exact: true })).toBeVisible();
  const login = page.getByRole("banner").getByRole("link", { name: "Googleでログイン" });
  await expect(login).toHaveAttribute("href", "/auth/login");
  // アップロードの代わりにログインの案内を出す（アクセス制御そのものは API が行う）
  await expect(page.getByLabel("文書のPDF（複数選べます）")).toHaveCount(0);
  await expect(page.getByText("文書を解析するには、岡山大学のGoogleアカウントでのログインが必要です", { exact: false })).toBeVisible();
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
  await expect(page.getByText("taro@s.okayama-u.ac.jp")).toBeVisible();
  await expect(page.getByLabel("文書のPDF（複数選べます）")).toBeVisible();
  await page.evaluate(() => window.sessionStorage.setItem("mongai:demo-status:x", "{}"));
  await page.getByRole("button", { name: "ログアウト" }).click();
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
  await page.getByRole("button", { name: "ゲストに戻る" }).click();
  await expect(page.getByText("ゲスト", { exact: true })).toBeVisible();
  expect(api.logouts).toBe(1);
});

test("ログアウトを確定できなかったときは、ゲストに切り替えずに知らせる", async ({ page }) => {
  const api = await mockApi(page, { logoutStatus: 503 });
  await page.goto("/");
  await page.getByRole("button", { name: "ログアウト" }).click();
  await expect(page.getByRole("alert")).toContainText("ログアウトを確定できませんでした");
  await expect(page.getByText("taro@s.okayama-u.ac.jp")).toBeVisible();
  expect(api.logouts).toBe(1);
});
