export type DocumentType = "paper" | "course_report" | "lab_report";
export type ProfileScope = "faculty" | "course";
export type CheckKind = "content" | "character_count" | "reference_count" | "heading" | "style" | "manual";

export const DOCUMENT_LABELS: Record<DocumentType, string> = {
  paper: "論文", course_report: "授業レポート", lab_report: "実験レポート",
};
export const CHECK_LABELS: Record<CheckKind, string> = {
  content: "内容", character_count: "文字数", reference_count: "参考文献数",
  heading: "指定見出し", style: "文体", manual: "手元で確認する条件",
};
export const VERDICT_LABELS = { met: "満たす", not_met: "満たさない", unknown: "判断できない" };

export interface Requirement {
  requirement_id: string;
  text: string;
  source: string;
  source_excerpt: string;
  strength: "required" | "recommended";
  kind: CheckKind;
  minimum: number | null;
  maximum: number | null;
  value: string | null;
  include_spaces: boolean;
  count_scope: "body" | "all";
  aggregation: "all" | "any";
  enabled: boolean;
}
export interface InstructionProfile {
  profile_id: string;
  name: string;
  scope: ProfileScope;
  raw_text: string;
  requirements: Requirement[];
  revision: number;
  updated_at: string;
}
export interface InstructionDraft {
  draft_id: string;
  document_type: DocumentType;
  raw_text: string;
  profiles: InstructionProfile[];
  requirements: Requirement[];
  conflicts: { requirement_ids: string[]; reason: string }[];
  status: "queued" | "running" | "ready" | "failed";
  error: string | null;
  revision: number;
  confirmed: boolean;
  legacy_rules: { rule_id: string; rule: string }[];
  excluded_rule_ids: string[];
}
export interface InstructionSnapshot {
  schema_version: number;
  document_type: DocumentType;
  profiles: InstructionProfile[];
  raw_text: string;
  requirements: Requirement[];
  excluded_rule_ids: string[];
}
export interface RequirementResult {
  requirement_id: string;
  verdict: keyof typeof VERDICT_LABELS;
  reason: string;
  sentence_ids: string[];
  paragraph_ids: string[];
  pages: number[];
  quote: string;
  coverage: "full" | "partial" | "none";
  finding_id: string | null;
  state: "pending" | "completed";
  suggestion: string;
}
export interface RequirementsResponse {
  snapshot: InstructionSnapshot | null;
  results: RequirementResult[];
}
export interface InstructionSelection {
  ready: boolean;
  instruction_draft_id?: string;
  instruction_revision?: number;
}
export interface AnalysisOptions {
  document_type?: DocumentType;
  instruction_draft_id?: string;
  instruction_revision?: number;
}

/** 確認文を編集したら、古い機械判定の数値を流用せず内容の判定に戻す。 */
export function editRequirementText(item: Requirement, text: string): Requirement {
  return { ...item, text, kind: "content", minimum: null, maximum: null, value: null };
}

/** 表示する条件と、実際に照合する数値・指定値をそろえる。原文の記録は残す。 */
export function editRequirementParameters(item: Requirement, values: Partial<Requirement>): Requirement {
  const next = { ...item, ...values };
  if (next.kind === "character_count" || next.kind === "reference_count") {
    const unit = next.kind === "character_count" ? "文字" : "件";
    const range = [next.minimum === null ? "" : `${next.minimum}${unit}以上`,
      next.maximum === null ? "" : `${next.maximum}${unit}以内`].filter(Boolean).join("、");
    const label = next.kind === "character_count" ? `${next.count_scope === "body" ? "抽出本文" : "全抽出段落"}の文字数` : "参考文献数";
    next.text = `${label}：${range || "範囲を指定してください"}${next.kind === "character_count" ? `（空白${next.include_spaces ? "を含む" : "を除く"}）` : ""}`;
  } else if (next.kind === "heading") next.text = `「${next.value ?? ""}」の見出しを含める`;
  else if (next.kind === "style") next.text = `文体を「${next.value ?? ""}」にそろえる`;
  return next;
}
