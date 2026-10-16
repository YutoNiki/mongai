// 書体（自前で配信する。役割と太さの根拠は docs/typography.md）。使う太さ・文字集合だけを読み込む。
// 日本語の書体は文字の範囲（unicode-range）ごとに分かれていて、ブラウザは画面で使う字の分だけを取得する
import "@fontsource/biz-udpgothic/400.css"; // 本文・操作
import "@fontsource/biz-udpgothic/700.css";
import "@fontsource/zen-kaku-gothic-new/700.css"; // 見出し
import "@fontsource/ibm-plex-sans/latin-400.css"; // 数値（英数字だけ）
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-sans/latin-700.css";
import "@fontsource/noto-sans-jp/400.css"; // 本文の書体に無い漢字の補完
import "@fontsource/noto-sans-jp/700.css";
import "./theme/typography.css";
import "./theme/global.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { applyPalette } from "./theme/palette";

applyPalette(document.documentElement);

// PWA：画面の部品だけをキャッシュする Service Worker を登録する（HTTPS か localhost のときだけ有効）
if (import.meta.env.PROD && "serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
