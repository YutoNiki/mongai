/**
 * 文字（docs/typography.md）の E2E テスト：役割ごとの書体、書体の配信元、CSP、グラフの書体、文字を大きくしたときの崩れ。
 */

import { expect, test, type Page } from "@playwright/test";

import { analysisId, mockApi } from "./mockApi";

const ORIGIN = "127.0.0.1:4173";

// 本番の nginx が付ける CSP（docker/web/nginx.conf と同じ値）。vite preview は CSP を付けないので、画面の応答に付けて確かめる
const CSP =
  "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'";

interface FontLog {
  requests: string[];
  failures: string[];
}

/** 書体のファイルの要求と、失敗（404 など）を記録する */
function watchFonts(page: Page): FontLog {
  const log: FontLog = { requests: [], failures: [] };
  page.on("request", (r) => {
    if (r.resourceType() === "font" || /\.(woff2?|ttf|otf)(\?|$)/.test(r.url())) log.requests.push(r.url());
  });
  page.on("requestfailed", (r) => {
    if (r.resourceType() === "font") log.failures.push(r.url());
  });
  page.on("response", (r) => {
    if (r.request().resourceType() === "font" && r.status() !== 200) log.failures.push(`${r.status()} ${r.url()}`);
  });
  return log;
}

/** 画面（HTML）の応答に本番と同じ CSP を付け、違反を window.__csp に記録する */
async function enforceCsp(page: Page): Promise<void> {
  await page.context().route("**/*", async (route) => {
    if (route.request().resourceType() !== "document") return route.fallback();
    const response = await route.fetch();
    await route.fulfill({ response, headers: { ...response.headers(), "content-security-policy": CSP } });
  });
  await page.addInitScript(() => {
    const w = window as unknown as { __csp: string[] };
    w.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) => w.__csp.push(`${e.violatedDirective} ${e.blockedURI}`));
  });
}

/** 要素の計算済みの書体の先頭と、数字の表示 */
async function fontOf(page: Page, selector: string): Promise<{ family: string; numeric: string; size: number }> {
  return page.locator(selector).first().evaluate((el) => {
    const s = getComputedStyle(el);
    return {
      family: (s.fontFamily.split(",")[0] ?? "").replace(/"/g, "").trim(),
      numeric: s.fontVariantNumeric,
      size: Number.parseFloat(s.fontSize),
    };
  });
}

test("役割ごとの書体：説明文は Noto Sans JP、操作は BIZ UDPGothic、見出しは Zen Kaku Gothic New、数値は IBM Plex Sans（等幅の数字）", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto(`/analyses/${analysisId}`);
  await expect(page.locator(".kpi-value")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);

  for (const selector of ["body", ".summary-text p", ".data-table td:not(.num)", ".meta dt"]) {
    expect((await fontOf(page, selector)).family, selector).toBe("Noto Sans JP");
  }
  for (const selector of ["button", "select", ".site-nav a", "label", ".badge", ".data-table thead th", ".button-secondary"]) {
    expect((await fontOf(page, selector)).family, selector).toBe("BIZ UDPGothic");
  }
  for (const selector of ["h1", "h2.panel-title"]) {
    expect((await fontOf(page, selector)).family, selector).toBe("Zen Kaku Gothic New");
  }
  for (const selector of [".kpi-value", ".kpi-breakdown dd", ".data-table td.num", ".meta dd.numeric"]) {
    const font = await fontOf(page, selector);
    expect(font.family, selector).toBe("IBM Plex Sans");
    expect(font.numeric, selector).toContain("tabular-nums");
  }
  // 補助情報も 14px 以上
  for (const selector of [".note", ".data-table thead th", ".meta dl", ".badge"]) {
    expect((await fontOf(page, selector)).size, selector).toBeGreaterThanOrEqual(14);
  }
  // 画面で使った書体が実際に読み込まれている（フォールバックの書体のままではない）
  const loaded = await page.evaluate(() =>
    [...document.fonts].filter((f) => f.status === "loaded").map((f) => `${f.family.replace(/"/g, "")} ${f.weight}`),
  );
  for (const face of ["Noto Sans JP 400", "BIZ UDPGothic 400", "BIZ UDPGothic 700", "Zen Kaku Gothic New 700", "IBM Plex Sans 600"]) {
    expect(loaded, face).toContain(face);
  }
});

test("書体はすべて同じオリジンから取得し、外部（Google Fonts など）や存在しないファイルを要求せず、CSP に違反しない", async ({
  page,
}) => {
  await mockApi(page);
  await enforceCsp(page);
  const fonts = watchFonts(page);
  const external: string[] = [];
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (!["data:", "blob:"].includes(url.protocol) && url.host !== ORIGIN) external.push(r.url());
  });
  for (const path of ["/", `/analyses/${analysisId}`, `/analyses/${analysisId}/findings/F-0001`, "/rules"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
  }
  expect(fonts.requests.length).toBeGreaterThan(0);
  for (const url of fonts.requests) {
    expect(new URL(url).host, url).toBe(ORIGIN);
    expect(url, url).toMatch(/\/assets\/[^/]+\.woff2$/);
  }
  expect(fonts.failures).toEqual([]);
  expect(external).toEqual([]);
  expect(await page.evaluate(() => (window as unknown as { __csp: string[] }).__csp)).toEqual([]);
});

test("グラフ（Canvas）は書体の読み込みを待ってから描く", async ({ page }) => {
  await mockApi(page);
  // 書体の取得を遅らせ、読み込み前にグラフを描いていないかを確かめる
  await page.context().route(/\.woff2$/, async (route) => {
    await new Promise((r) => setTimeout(r, 700));
    await route.fallback();
  });
  await page.addInitScript(() => {
    const w = window as unknown as { __chartFonts: boolean[] };
    w.__chartFonts = [];
    new MutationObserver((records) => {
      for (const r of records)
        for (const node of r.addedNodes)
          if (node instanceof HTMLCanvasElement && node.closest("figure"))
            w.__chartFonts.push(
              document.fonts.check('400 14px "IBM Plex Sans"', "0123456789") &&
                document.fonts.check('400 14px "BIZ UDPGothic"', "論理") &&
                document.fonts.check('600 14px "Noto Sans JP"', "件"),
            );
    }).observe(document, { childList: true, subtree: true });
  });
  await page.goto(`/analyses/${analysisId}`);
  await expect(page.locator("figure canvas")).toHaveCount(2);
  const atDraw = await page.evaluate(() => (window as unknown as { __chartFonts: boolean[] }).__chartFonts);
  expect(atDraw.length).toBeGreaterThan(0);
  expect(atDraw.every(Boolean)).toBe(true);
});

test("ブラウザの文字サイズを大きくしても（150%・200%）、画面が横にはみ出さない", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "既定の文字の大きさの変更は Chromium の機能で行う");
  await mockApi(page);
  const cdp = await page.context().newCDPSession(page);
  for (const standard of [24, 32]) {
    // ブラウザの設定の「フォントサイズ」と同じ。rem の文字も、em で書いた区切りの幅も大きくなる
    await cdp.send("Page.setFontSizes", { fontSizes: { standard, fixed: standard - 3 } });
    for (const path of ["/", `/analyses/${analysisId}`, "/rules", "/lab", "/instructions"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      const body = await fontOf(page, "body");
      expect(body.size, `${path} ${standard}px`).toBe(standard);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${path} ${standard}px`).toBeLessThanOrEqual(0);
    }
  }
});
