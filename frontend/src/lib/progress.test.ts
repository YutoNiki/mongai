import { describe, expect, it } from "vitest";

import { elapsedText, formatDuration, overallPercent } from "./progress";

describe("overallPercent", () => {
  it("順番待ちは0、完了は100", () => {
    expect(overallPercent("queued", { stage: null, done: 0, total: 0 })).toBe(0);
    expect(overallPercent("completed", { stage: "locate", done: 3, total: 3 })).toBe(100);
  });

  it("前の工程の分に、今の工程の進み具合を足す", () => {
    expect(overallPercent("running", { stage: "ingest", done: 0, total: 1 })).toBe(0);
    expect(overallPercent("running", { stage: "paragraph_check", done: 0, total: 10 })).toBe(13);
    expect(overallPercent("running", { stage: "paragraph_check", done: 5, total: 10 })).toBe(43);
    expect(overallPercent("running", { stage: "locate", done: 1, total: 1 })).toBe(99);
  });

  it("工程が増えても進捗は戻らない", () => {
    const stages = ["ingest", "structure", "paragraph_check", "flow_check", "citation", "double_check", "suggestion", "locate"];
    const values = stages.map((stage) => overallPercent("running", { stage, done: 0, total: 1 }));
    expect(values).toEqual([...values].sort((a, b) => a - b));
  });
});

describe("formatDuration", () => {
  it("分と時間で表す", () => {
    expect(formatDuration(30_000)).toBe("1分未満");
    expect(formatDuration(5 * 60_000)).toBe("5分");
    expect(formatDuration(60 * 60_000)).toBe("1時間");
    expect(formatDuration(125 * 60_000)).toBe("2時間5分");
  });
});

describe("elapsedText", () => {
  it("終わった解析は終了まで、解析中は今まで", () => {
    const start = "2026-10-13T03:00:00+00:00";
    expect(elapsedText(start, "2026-10-13T04:05:00+00:00", 0)).toBe("1時間5分");
    expect(elapsedText(start, null, Date.parse("2026-10-13T03:12:30+00:00"))).toBe("12分");
    expect(elapsedText("不明", null, 0)).toBeNull();
  });
});
