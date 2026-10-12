import { describe, expect, it } from "vitest";

import type { Counts } from "./api";
import { csvRow, diffText, summaryText, toCsv } from "./summary";

const counts: Counts = {
  total: 5,
  by_severity: { critical: 1, major: 3, suggestion: 1 },
  by_category: { logic: 3, readability: 2, rule: 0, citation: 0, lab: 0 },
  by_section: [
    { key: "1", label: "1 はじめに", count: 1 },
    { key: "3", label: "3 実験", count: 4 },
  ],
  section_of: {},
  by_status: { open: 5 },
  by_confidence: { high: 2, low: 3 },
};

describe("summaryText", () => {
  it("要点を文章にする", () => {
    expect(summaryText(counts, 8)).toBe(
      "指摘は全部で5件です（重大 1件、要修正 3件、提案 1件）。" +
        "カテゴリ別では「論理」が3件で最も多くなっています。" +
        "セクション別では「3 実験」が4件で最も多くなっています。前回比 −3件です。",
    );
  });
  it("指摘が無い場合", () => {
    expect(summaryText({ ...counts, total: 0 })).toBe("指摘はありません。");
  });
});

describe("diffText", () => {
  it("増減を符号付きで示す", () => {
    expect(diffText(5, 2)).toBe("前回比 +3件");
    expect(diffText(5, 5)).toBe("前回比 ±0件");
    expect(diffText(5, undefined)).toBeNull();
  });
});

describe("CSV", () => {
  it("区切り文字や引用符を含む値を引用符で囲む", () => {
    expect(csvRow(["a", 'b"c', "d,e", 3])).toBe('a,"b""c","d,e",3');
    expect(toCsv(["項目", "件数"], [["論理", 3]])).toBe("﻿項目,件数\r\n論理,3\r\n");
  });
});
