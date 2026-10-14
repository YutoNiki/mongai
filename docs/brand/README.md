# SI-NSA ブランド素材（案B：閉域と網掛け）

リポジトリのルートで展開すると、各ファイルが所定の場所に入ります。

| 場所 | 内容 |
|---|---|
| `frontend/public/` | `favicon.ico`（16/32/48）、`favicon.svg`、`apple-touch-icon.png`（180）、`manifest.webmanifest` |
| `frontend/public/icons/` | PWAアイコン 192/512（`any` は角丸、`maskable` は全面塗り＋安全領域内に縮小） |
| `frontend/src/assets/brand/` | ロゴ・バナー・マークの SVG（文字はアウトライン化済み。フォント不要） |
| `docs/brand/` | README やスライド用の PNG（2倍解像度） |

## `index.html` の `<head>` に入れるもの

```html
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#000060">
```

## 使い分け

- 明るい背景：`logo.svg` / `banner.svg` / `mark.svg`
- 暗い背景（`#000060` など）：`*-dark.svg`
- 16〜48px の小さい表示：`favicon.svg`（行を省いた簡略版）

## 色

| 用途 | 色 |
|---|---|
| 紺（キーカラー） | `#000060` |
| 行（青） | `#7096F8` |
| 網掛け（黄） | `#FFC700` |
| タグライン（明るい背景） | `#3D3D50` |
| タグライン（暗い背景） | `#D9E6FF` |

紺と青はデジタル庁ダッシュボードガイドブック（2026年3月版）の Blue パレットの値。7月の更新値に合わせる場合は SVG 内の色を置き換える。

## フォント

ロゴタイプは Sora Bold、タグラインは Noto Sans JP Regular（どちらも SIL Open Font License）。SVG ではアウトライン化しているため、フォントの読み込みは不要。
