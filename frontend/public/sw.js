/*
 * MongAI の Service Worker（PWA）。
 *
 * 画面の部品（HTML・JS・CSS・アイコン・フォント・PDF.js の部品）だけをキャッシュする。
 * 論文・解析結果（/api 以下）は端末に保存しない（キャッシュしない）。
 */
// 中身を変えたら番号を上げる（古いキャッシュは activate で消える）
const CACHE = "mongai-shell-v2";
const SHELL = ["/", "/manifest.webmanifest", "/favicon.ico", "/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  // 同じオリジンの GET だけを扱う。API（論文・解析結果）は常にネットワークから取り、保存しない
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) {
    return;
  }
  // 画面の遷移：ネットワークを優先し、つながらないときだけ保存した画面を返す
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put("/", copy));
          return response;
        })
        .catch(() => caches.match("/")),
    );
    return;
  }
  // ファイル名にハッシュが付く部品（ロゴを含む）と PDF.js の部品：キャッシュを優先する
  if (/^\/(assets|pdfjs)\//.test(url.pathname)) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(CACHE).then((cache) => cache.put(request, copy));
            }
            return response;
          }),
      ),
    );
    return;
  }
  // アイコン・ファビコン（ファイル名が変わらない）：ネットワークを優先し、つながらないときだけ保存したものを返す
  if (/^\/(icons\/|favicon\.ico$|manifest\.webmanifest$)/.test(url.pathname)) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || Response.error())),
    );
  }
});
