import { defineConfig, devices } from "@playwright/test";

// E2E テスト（SPEC 7）。ネットワークなしのコンテナ（make test-e2e）で、ビルド済みの画面を vite preview で
// 配信して操作する。/api はテストの中で差し替える（e2e/mockApi.ts）ので、core や おいとまAPI は使わない。
const PORT = 4173;

export default defineConfig({
  testDir: "e2e",
  outputDir: "/tmp/playwright-results",
  fullyParallel: true,
  forbidOnly: true,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    locale: "ja-JP",
    timezoneId: "Asia/Tokyo",
    trace: "off",
    // PWA の Service Worker が /api への要求を横取りすると、テストの差し替え（page.route）が効かないため
    serviceWorkers: "block",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 900 } } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
  },
});
