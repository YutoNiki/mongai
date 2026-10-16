/**
 * 文字の定義（theme/typography.css の変数）を JavaScript から読む。
 *
 * Canvas に描くグラフ（ECharts）は CSS の書体の指定を継がないため、同じ変数の値を読んで渡す（値をここに書き写さない）。
 * また、書体の読み込み前に描くとフォールバックの書体で描かれ、ECharts が測った文字幅も残るため、
 * 必要な書体の読み込みを待ってから描く（whenFontsLoaded）。
 */

/** "A", "B", sans-serif のような書体の並びの、先頭の書体（読み込みを待つ対象） */
export function primaryFamily(stack: string): string {
  return stack.split(",")[0]?.trim() ?? "";
}

/** CSS の長さ（rem か px）を px に直す。rem は文書の根元の文字の大きさ（ブラウザの設定に従う）を掛ける */
export function lengthToPx(value: string, rootFontPx: number): number {
  const v = value.trim();
  const n = Number.parseFloat(v);
  if (Number.isNaN(n)) return 0;
  return v.endsWith("rem") ? n * rootFontPx : n;
}

export interface ChartTypography {
  /** 項目名・単位など（本文の書体） */
  bodyFamily: string;
  /** 目盛り・値（数値の書体） */
  numericFamily: string;
  /** グラフの文字の大きさ（補助情報と同じ）。px */
  fontSize: number;
  regular: number;
  numericStrong: number;
  /** 文書の根元の文字の大きさ。px */
  rootFontPx: number;
}

/** グラフに使う文字の値を、typography.css の変数から読む */
export function readChartTypography(root: HTMLElement = document.documentElement): ChartTypography {
  const style = getComputedStyle(root);
  const token = (name: string) => style.getPropertyValue(name).trim();
  const rootFontPx = Number.parseFloat(style.fontSize) || 16;
  return {
    bodyFamily: token("--font-family-body"),
    numericFamily: token("--font-family-numeric"),
    fontSize: lengthToPx(token("--font-size-caption"), rootFontPx),
    regular: Number(token("--font-weight-regular")) || 400,
    numericStrong: Number(token("--font-weight-numeric-strong")) || 600,
    rootFontPx,
  };
}

/** 読み込みを待つ書体と、その書体で描く文字（日本語の書体は文字の範囲ごとに分かれているので、描く字を渡す） */
export interface FontRequest {
  /** CSS の font の値（例：400 14px "BIZ UDPGothic"） */
  font: string;
  text: string;
}

/**
 * 書体が読み込み済みならすぐに、まだなら読み込みを待ってから解決する。読み込みに失敗しても解決する（フォールバックの書体で描く）。
 * 閉域でつながりにくいときに描画が止まらないよう、timeoutMs で待つのをやめる。
 */
export function whenFontsLoaded(requests: FontRequest[], timeoutMs = 3000): Promise<void> {
  if (typeof document === "undefined" || !("fonts" in document)) return Promise.resolve();
  const fonts = document.fonts;
  if (requests.every((r) => fonts.check(r.font, r.text))) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, timeoutMs);
    void Promise.all(requests.map((r) => fonts.load(r.font, r.text).catch(() => []))).then(() => {
      clearTimeout(timer);
      resolve();
    });
  });
}
