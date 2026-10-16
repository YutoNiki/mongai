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

// 書体（@fontsource）の CSS から WOFF の指定を除き、WOFF2 だけを配信物に含める（対応ブラウザはすべて WOFF2 を読める。
// WOFF も書いておくと、使われないファイルが配信物に同じ数だけ増える）
function woff2Only(): Plugin {
  return {
    name: "fontsource-woff2-only",
    enforce: "pre",
    transform(code, id) {
      if (!/[\\/]@fontsource[\\/].+\.css$/.test(id)) return null;
      return code.replace(/,\s*url\([^)]+\.woff\) format\('woff'\)/g, "");
    },
  };
}

// 書体のライセンス（SIL Open Font License 1.1）の写しを、書体と一緒に配信物に含める
function copyFontLicenses(): Plugin {
  let outDir = resolve(__dirname, "dist");
  return {
    name: "copy-font-licenses",
    apply: "build",
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      for (const name of ["biz-udpgothic", "zen-kaku-gothic-new", "ibm-plex-sans", "noto-sans-jp"]) {
        cpSync(resolve(__dirname, "node_modules/@fontsource", name, "LICENSE"), resolve(outDir, "licenses/fonts", `${name}.txt`));
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), copyPdfjsAssets(), woff2Only(), copyFontLicenses()],
  // 読み取り専用のテスト用コンテナでは /tmp にキャッシュを置く
  cacheDir: process.env.VITE_CACHE_DIR ?? "node_modules/.vite",
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 4000,
    // 小さな書体のファイルも data: URI にせず、ファイルとして配信する（CSP の font-src 'self' は data: を許さない）
    assetsInlineLimit: (file) => (/\.woff2?$/.test(file) ? false : undefined),
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
