import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { lengthToPx, primaryFamily, whenFontsLoaded } from "./typography";

const css = (name: string) => readFileSync(resolve(import.meta.dirname, name), "utf-8");

/** typography.css の、ある範囲（:root か、スマートフォンの @media の中）の変数 */
function tokens(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1] ?? "", (m[2] ?? "").trim()]));
}

describe("typography.css（文字の定義）", () => {
  const source = css("typography.css");
  const root = tokens(source.slice(source.indexOf(":root {"), source.indexOf("@media (max-width")));
  const mobile = { ...root, ...tokens(source.slice(source.indexOf("@media (max-width"), source.indexOf("/* ---- 要素の既定"))) };

  it("文字の大きさは rem で書き、補助情報でも 14px（0.875rem）以上にする", () => {
    for (const set of [root, mobile]) {
      const sizes = Object.entries(set).filter(([k]) => k.startsWith("--font-size-"));
      expect(sizes.length).toBeGreaterThan(10);
      for (const [name, value] of sizes) {
        expect(value, name).toMatch(/^[\d.]+rem$/);
        expect(lengthToPx(value, 16), name).toBeGreaterThanOrEqual(14);
      }
    }
  });

  it("スマートフォンの入力欄・ボタン・メニューは 16px 以上（iOS が入力時に拡大しない・押しやすい）", () => {
    for (const name of ["--font-size-input", "--font-size-button", "--font-size-nav", "--font-size-body"]) {
      expect(lengthToPx(mobile[name] ?? "", 16), name).toBeGreaterThanOrEqual(16);
    }
  });

  it("行間：本文 1.65〜1.75、見出し 1.3〜1.4、操作 1.4〜1.5", () => {
    const lh = (name: string) => Number(root[name]);
    expect(lh("--line-height-body")).toBeGreaterThanOrEqual(1.65);
    expect(lh("--line-height-body")).toBeLessThanOrEqual(1.75);
    expect(lh("--line-height-heading")).toBeGreaterThanOrEqual(1.3);
    expect(lh("--line-height-heading")).toBeLessThanOrEqual(1.4);
    expect(lh("--line-height-ui")).toBeGreaterThanOrEqual(1.4);
    expect(lh("--line-height-ui")).toBeLessThanOrEqual(1.5);
  });

  it("書体は自前で配信する書体だけを並べる（OS の書体名を書かない。最後は総称の sans-serif）", () => {
    const served = ["BIZ UDPGothic", "Zen Kaku Gothic New", "IBM Plex Sans", "Noto Sans JP"];
    for (const name of ["--font-family-body", "--font-family-heading", "--font-family-numeric"]) {
      const families = (root[name] ?? "").split(",").map((f) => f.trim().replace(/"/g, ""));
      expect(families.at(-1), name).toBe("sans-serif");
      for (const f of families.slice(0, -1)) expect(served, `${name}: ${f}`).toContain(f);
    }
  });

  it("ほかの CSS は文字の値を直書きせず、typography.css の変数を使う", () => {
    const global = css("global.css").replace(/\/\*[\s\S]*?\*\//g, "");
    expect(global).not.toMatch(/font-size:\s*\d/);
    expect(global).not.toMatch(/font-weight:\s*(\d|bold|normal)/);
    expect(global).not.toMatch(/line-height:\s*\d/);
    expect(global).not.toMatch(/font-family:(?!\s*var\()/);
  });
});

describe("typography.ts", () => {
  it("書体の並びの先頭を取り出す", () => {
    expect(primaryFamily('"IBM Plex Sans", "BIZ UDPGothic", sans-serif')).toBe('"IBM Plex Sans"');
    expect(primaryFamily(" sans-serif")).toBe("sans-serif");
  });

  it("rem は根元の文字の大きさを掛けて px にする（ブラウザの文字サイズの設定に従う）", () => {
    expect(lengthToPx("0.875rem", 16)).toBe(14);
    expect(lengthToPx("0.875rem", 20)).toBe(17.5);
    expect(lengthToPx("13px", 16)).toBe(13);
    expect(lengthToPx("", 16)).toBe(0);
  });
});

describe("whenFontsLoaded（グラフを書体の読み込み後に描く）", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });
  const request = [{ font: '400 14px "BIZ UDPGothic"', text: "論理" }];

  it("読み込み済みなら読み込みを始めずにすぐ解決する", async () => {
    const load = vi.fn();
    vi.stubGlobal("document", { fonts: { check: () => true, load } });
    await whenFontsLoaded(request);
    expect(load).not.toHaveBeenCalled();
  });

  it("未読み込みなら、描く字を渡して読み込みを待つ", async () => {
    const load = vi.fn(() => Promise.resolve([]));
    vi.stubGlobal("document", { fonts: { check: () => false, load } });
    await whenFontsLoaded(request);
    expect(load).toHaveBeenCalledWith('400 14px "BIZ UDPGothic"', "論理");
  });

  it("読み込みに失敗しても、終わらなくても、描画を止めない（待つのは timeoutMs まで）", async () => {
    vi.stubGlobal("document", { fonts: { check: () => false, load: () => Promise.reject(new Error("offline")) } });
    await expect(whenFontsLoaded(request)).resolves.toBeUndefined();

    vi.useFakeTimers();
    vi.stubGlobal("document", { fonts: { check: () => false, load: () => new Promise(() => undefined) } });
    const waiting = whenFontsLoaded(request, 3000);
    vi.advanceTimersByTime(3000);
    await expect(waiting).resolves.toBeUndefined();
  });
});
