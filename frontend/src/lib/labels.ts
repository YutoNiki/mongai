/** 表示名（core/annotate/labels.py と同じ）。UI の文言は日本語。 */

export const CATEGORY_LABELS: Record<string, string> = {
  logic: "論理",
  readability: "読みやすさ",
  rule: "ルール違反",
  citation: "引用",
  lab: "研究室知識",
};

export const SUBCATEGORY_LABELS: Record<string, string> = {
  premise_evidence_mismatch: "前提と根拠の不一致",
  leap: "論理の飛躍",
  overclaim: "主張の過大",
  subject_predicate_mismatch: "主語と述語のねじれ",
  ambiguous_reference: "指示語の曖昧さ",
  disconnection: "段落間の接続の断絶",
  section_role_violation: "セクションの役割違反",
};

export const SEVERITY_LABELS: Record<string, string> = {
  critical: "重大",
  major: "要修正",
  suggestion: "提案",
};

export const SHAPE_LABELS: Record<string, string> = {
  logic: "下線",
  readability: "網掛け",
  rule: "波線",
  citation: "枠",
  lab: "点線の枠",
};

export const STATUS_LABELS: Record<string, string> = {
  open: "未対応",
  accepted: "対応済み",
  dismissed: "却下",
};

export const STAGE_LABELS: Record<string, string> = {
  queued: "順番待ち",
  ingest: "PDFの構造化",
  structure: "論証構造の抽出",
  paragraph_check: "段落内のチェック",
  flow_check: "段落間のチェック",
  suggestion: "改善案の作成",
};

export const STAGES = ["ingest", "structure", "paragraph_check", "flow_check", "suggestion"] as const;
export const SEVERITIES = ["critical", "major", "suggestion"] as const;
export const CATEGORIES = ["logic", "readability", "rule", "citation", "lab"] as const;

export function label(map: Record<string, string>, key: string): string {
  return map[key] ?? key;
}
