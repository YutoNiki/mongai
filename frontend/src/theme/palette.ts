/**
 * 色の定義（フロントエンドの唯一の定義元）。core/annotate/palette.py と値を揃える。
 *
 * 出典（2026-10-13 取得）：デジタル庁「ダッシュボードデザインの実践ガイドブック」カラーパレット
 * （2026年7月17日更新）の Blue（GitHub digital-go-jp/policy-dashboard-assets の
 * CustomThemeBlueDefault_2026.json）。段階名はデジタル庁デザインシステムのデザイントークンで確認した。
 */

/** キーカラー Blue のパレット */
export const BLUE = {
  b50: "#E8F1FE",
  b200: "#C5D7FB",
  b400: "#7096F8",
  b600: "#3460FB",
  b900: "#0017C1",
  b1200: "#000060",
} as const;

export const colors = {
  /** テキスト */
  text: "#000000",
  textOnDark: "#FFFFFF",
  label: "#626264",
  link: BLUE.b900,
  /** 背景 */
  background: "#FFFFFF",
  backgroundStandard: "#F8F8FB",
  backgroundHighlight: BLUE.b50,
  backgroundControl: "#F1F1F4",
  border: "#949494",
  /** チャート（1〜5色に絞る） */
  chartPrimary: BLUE.b900,
  chartSecondary: BLUE.b600,
  chartNeutral: "#949494",
  /** セマンティックカラー */
  negative: "#FE3939",
  error: "#CE0000",
  success: "#197A4B",
  focus: "#FFD43D",
} as const;

/** 指摘のカテゴリ → 注釈の色（core/annotate/palette.py の ANNOTATION_COLORS と同じ値） */
export const annotationColors: Record<string, string> = {
  logic: "#CE0000",
  readability: "#A58000",
  rule: "#0017C1",
  citation: "#AC3E00",
  lab: "#006F83",
};

/** CSS 変数として文書に設定する（CSS 側はこの変数だけを使う） */
export function applyPalette(root: HTMLElement): void {
  const vars: Record<string, string> = {
    "--color-text": colors.text,
    "--color-text-on-dark": colors.textOnDark,
    "--color-label": colors.label,
    "--color-link": colors.link,
    "--color-bg": colors.background,
    "--color-bg-standard": colors.backgroundStandard,
    "--color-bg-highlight": colors.backgroundHighlight,
    "--color-bg-control": colors.backgroundControl,
    "--color-border": colors.border,
    "--color-primary": colors.chartPrimary,
    "--color-error": colors.error,
    "--color-success": colors.success,
    "--color-focus": colors.focus,
  };
  for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
}
