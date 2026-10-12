"""ロゴ・アイコンの元画像（frontend/design/）から、Web 用の画像（frontend/public/）を作る。

- header.jpeg      → 余白をトリミングして、ヘッダー用のロゴ（brand/header-logo.png）
- icon_fabicon.jpeg（左：アプリアイコン、右：ファビコン、上に見出しの文字がある）
    → アプリアイコン：PWA 用（192・512・マスカブル 512）と iPhone のホーム画面用（180）
    → ファビコン：背景の白を透過にして、favicon.ico（16・32・48）と PNG（32・192）

生成した画像はリポジトリに含める。元画像を差し替えたときだけ、次のように実行し直す（Pillow と numpy が必要）：
    python scripts/make_brand_images.py
"""

from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "design"
OUT = ROOT / "frontend" / "public"
WHITE_THRESHOLD = 235  # これより明るい画素は背景（JPEG のノイズを含む白）とみなす


def content_box(img: Image.Image, threshold: int = WHITE_THRESHOLD) -> tuple[int, int, int, int]:
    """背景（白）以外の画素を囲む矩形。"""
    a = np.asarray(img.convert("L"))
    ys, xs = np.where(a < threshold)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def trim(img: Image.Image, pad: int = 0) -> Image.Image:
    x0, y0, x1, y1 = content_box(img)
    return img.crop((max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad)))


def white_to_alpha(img: Image.Image) -> Image.Image:
    """白い背景を透過にする（縁のなめらかさを保つため、白との差から透明度を決める）。"""
    rgb = np.asarray(img.convert("RGB")).astype(np.float64) / 255.0
    alpha = 1.0 - rgb.min(axis=2)
    alpha = np.clip((alpha - 0.04) / 0.96, 0.0, 1.0)  # わずかなノイズは完全に透過
    safe = np.where(alpha > 0, alpha, 1.0)[..., None]
    color = np.clip((rgb - (1.0 - alpha[..., None])) / safe, 0.0, 1.0)
    rgba = np.dstack([color, alpha]) * 255
    return Image.fromarray(rgba.round().astype(np.uint8), "RGBA")


def square(img: Image.Image, background: tuple[int, int, int, int]) -> Image.Image:
    size = max(img.size)
    canvas = Image.new("RGBA", (size, size), background)
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img if img.mode == "RGBA" else None)
    return canvas


def rounded_icon(img: Image.Image, radius_ratio: float = 0.2) -> Image.Image:
    """青い四角（青が赤より十分に強い画素）の範囲を正方形に切り出し、角丸の外側を透過にする。"""
    rgb = np.asarray(img.convert("RGB")).astype(int)
    blue = (rgb[..., 2] - rgb[..., 0]) > 40
    ys, xs = np.where(blue)
    # JPEG の縁に残る白い線を除くため、検出した範囲より少し内側で切り出す
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    inset = int(min(x1 - x0, y1 - y0) * 0.012)
    box = img.crop((x0 + inset, y0 + inset, x1 - inset, y1 - inset)).convert("RGBA")
    size = max(box.size)
    box = box.resize((size, size), Image.Resampling.LANCZOS)
    # 角丸のマスク（4倍の解像度で描いて縮小し、縁をなめらかにする）
    from PIL import ImageDraw

    big = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, size * 4 - 1, size * 4 - 1), radius=int(size * 4 * radius_ratio), fill=255)
    box.putalpha(big.resize((size, size), Image.Resampling.LANCZOS))
    return box


def main() -> None:
    (OUT / "brand").mkdir(parents=True, exist_ok=True)
    (OUT / "icons").mkdir(parents=True, exist_ok=True)

    # ヘッダーのロゴ（高さ 2 倍表示に十分な解像度に縮小）
    header = trim(Image.open(SRC / "header.jpeg").convert("RGB"), pad=8)
    header = white_to_alpha(header)
    header.thumbnail((1400, 400), Image.Resampling.LANCZOS)
    header.save(OUT / "brand" / "header-logo.png", optimize=True)

    sheet = Image.open(SRC / "icon_fabicon.jpeg").convert("RGB")
    w, h = sheet.size
    # 見出しの文字（上の方）を避けて、左右の半分からそれぞれ切り出す
    top = int(h * 0.315)
    app = trim(sheet.crop((0, top, w // 2, int(h * 0.85))))
    fav = trim(sheet.crop((w // 2, top, w, int(h * 0.85))))

    # アプリアイコン：青い角丸の四角の範囲を色で検出して正方形に切り出し、角の外側を透過にする
    app_sq = rounded_icon(app)
    for size in (192, 512):
        app_sq.resize((size, size), Image.Resampling.LANCZOS).save(OUT / "icons" / f"icon-{size}.png", optimize=True)
    # 角を埋める地の色（四角の上辺の中央付近）
    arr = np.asarray(app_sq.convert("RGB"))
    bg = tuple(int(c) for c in arr[arr.shape[0] // 20, arr.shape[1] // 2])
    # iPhone のホーム画面用（角は端末側で丸められるので、透過した角を地の色で埋める）
    apple = Image.new("RGB", app_sq.size, bg)
    apple.paste(app_sq, (0, 0), app_sq)
    apple.resize((180, 180), Image.Resampling.LANCZOS).save(OUT / "icons" / "apple-touch-icon.png", optimize=True)
    # マスカブル：端末ごとに形を切り抜かれるので、全面を地の色で塗り、アイコンを安全領域（80%）に収める
    mask_canvas = Image.new("RGB", (512, 512), bg)
    inner = app_sq.resize((410, 410), Image.Resampling.LANCZOS)
    mask_canvas.paste(inner, (51, 51), inner)
    mask_canvas.save(OUT / "icons" / "icon-maskable-512.png", optimize=True)

    # ファビコン：背景を透過にして正方形にする
    fav_sq = square(white_to_alpha(fav), (255, 255, 255, 0))
    for size in (32, 192):
        fav_sq.resize((size, size), Image.Resampling.LANCZOS).save(OUT / "icons" / f"favicon-{size}.png", optimize=True)
    fav_sq.resize((256, 256), Image.Resampling.LANCZOS).save(
        OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)]
    )
    print("header", header.size, "app", app.size, "fav", fav.size, "maskable bg", bg)


if __name__ == "__main__":
    main()
