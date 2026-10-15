import { describe, expect, it } from "vitest";
import { editRequirementParameters, editRequirementText, type Requirement } from "./instructions";

describe("確認項目の編集", () => {
  it("文字数の文面を書き換えたとき、変更前の数値で判定しない", () => {
    const item: Requirement = {
      requirement_id: "REQ-1", text: "2000字以内", source: "講義A", source_excerpt: "2000字以内",
      strength: "required", kind: "character_count", minimum: null, maximum: 2000,
      value: null, include_spaces: false, count_scope: "body", aggregation: "all", enabled: true,
    };
    expect(editRequirementText(item, "1500字以内")).toMatchObject({
      text: "1500字以内", kind: "content", maximum: null, source_excerpt: "2000字以内",
    });
    expect(item.maximum).toBe(2000);
    const changed = editRequirementParameters(item, { maximum: 1500 });
    expect(changed.text).toContain("1500文字以内");
    expect(changed.maximum).toBe(1500);
    expect(changed.source_excerpt).toBe("2000字以内");
  });
});
