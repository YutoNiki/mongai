"""色の定義（PDF注釈用）。フロントエンドの frontend/src/theme/palette.ts と値を揃える。

出典（2026-10-13 取得）：
- デジタル庁「ダッシュボードデザインの実践ガイドブック」カラーパレット（2026年7月17日更新）
  https://www.digital.go.jp/resources/dashboard-guidebook/color-palette
  および GitHub digital-go-jp/policy-dashboard-assets の CustomTheme*Default_2026.json
- 段階名（900 など）は、デジタル庁デザインシステムのデザイントークン
  （GitHub digital-go-jp/design-tokens の --color-primitive-*）で確認した。
  ダッシュボード用パレット（7色）に Yellow は無いため、Yellow はデザイントークンから取る
"""

from typing import Final

# デザイントークンの基本色（注釈に使うものだけ）
RED_900: Final = "#CE0000"
BLUE_900: Final = "#0017C1"
ORANGE_900: Final = "#AC3E00"
CYAN_900: Final = "#006F83"
# 白地に対して 3:1 以上になる Yellow のうち、余裕を持たせて 800 を使う（700 は 3.02:1）
YELLOW_800: Final = "#A58000"

WHITE: Final = "#FFFFFF"
TEXT_BLACK: Final = "#000000"

# 指摘のカテゴリ → 注釈の色（SPEC 4章）
ANNOTATION_COLORS: Final[dict[str, str]] = {
    "logic": RED_900,
    "readability": YELLOW_800,
    "rule": BLUE_900,
    "citation": ORANGE_900,
    "lab": CYAN_900,
}

# 網掛け（読みやすさ）の不透明度
HIGHLIGHT_OPACITY: Final = 0.35


def hex_to_rgb(color: str) -> tuple[float, float, float]:
    """ "#RRGGBB" を 0〜1 の RGB に変換する（PyMuPDF の色指定の形式）。"""
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"色の形式が不正です：{color}")
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return (r, g, b)


def relative_luminance(color: str) -> float:
    """WCAG 2.x の相対輝度。"""

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = hex_to_rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: str, b: str = WHITE) -> float:
    """WCAG 2.x のコントラスト比（1〜21）。"""
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)
