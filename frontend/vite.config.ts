import { cpSync } from "node:fs";
import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

// PDF.js が実行時に読む文字マップ（日本語の PDF に必要）・標準フォント・WebAssembly・ICC プロファイルを
// ビルド成果物に含め、自分のサーバーから配信する（外部 CDN を使わない）
function copyPdfjsAssets(): Plugin {
  // make live では出力先を /dist に変えるため、設定の解決後の値を使う
  let outDir = resolve(__dirname, "dist");
  return {
    name: "copy-pdfjs-assets",
    apply: "build",
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      const src = resolve(__dirname, "node_modules/pdfjs-dist");
      for (const dir of ["cmaps", "standard_fonts", "wasm", "iccs"]) {
        cpSync(resolve(src, dir), resolve(outDir, "pdfjs", dir), { recursive: true });
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), copyPdfjsAssets()],
  // 読み取り専用のテスト用コンテナでは /tmp にキャッシュを置く
  cacheDir: process.env.VITE_CACHE_DIR ?? "node_modules/.vite",
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 4000 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
