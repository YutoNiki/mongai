import { describe, expect, it } from "vitest";

import { overallPercent } from "./progress";

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
    const stages = ["ingest", "structure", "paragraph_check", "flow_check", "citation", "suggestion", "locate"];
    const values = stages.map((stage) => overallPercent("running", { stage, done: 0, total: 1 }));
    expect(values).toEqual([...values].sort((a, b) => a - b));
  });
});
