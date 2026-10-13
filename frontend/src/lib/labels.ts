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
  bib_not_found: "書誌が見つからない",
  bib_mismatch: "書誌の不一致",
  citation_unsupported: "引用内容が支持されない",
  rule_violation: "ルールへの違反",
  abbreviation_definition: "略語の定義",
  figure_table_reference: "図表の参照",
  unit_consistency: "単位の表記ゆれ",
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
  citation: "引用の検証",
  rule_check: "ルールの判定",
  double_check: "二重チェック",
  suggestion: "改善案の作成",
  locate: "指摘の位置の確認",
};

export const STAGES = [
  "ingest",
  "structure",
  "paragraph_check",
  "flow_check",
  "citation",
  "rule_check",
  "double_check",
  "suggestion",
  "locate",
] as const;

/** 検索ゲートウェイの判定 */
export const DECISION_LABELS: Record<string, string> = {
  sent: "送信",
  approved: "承認して送信",
  cached: "再利用（送信なし）",
  pending: "承認待ち",
  rejected: "却下",
  blocked: "拒否",
  failed: "失敗",
};

export const REQUEST_KIND_LABELS: Record<string, string> = {
  bib_lookup: "書誌の照合",
  oa_fetch: "アブストラクトの取得",
  term_lookup: "用語の照会",
};

export const MODE_LABELS: Record<string, string> = {
  strict: "strict（すべて承認制）",
  standard: "standard（書誌の照合は自動、用語の照会は承認制）",
  offline: "offline（学外への通信をすべて停止）",
};
export const SEVERITIES = ["critical", "major", "suggestion"] as const;
export const CATEGORIES = ["logic", "readability", "rule", "citation", "lab"] as const;

export function label(map: Record<string, string>, key: string): string {
  return map[key] ?? key;
}

/** ルールのステータス・判定方法・対象（ルールの画面） */
export const RULE_STATUS_LABELS: Record<string, string> = {
  pending: "未確認",
  approved: "承認済み",
  disabled: "無効",
};

export const CHECK_TYPE_LABELS: Record<string, string> = {
  mechanical: "機械判定",
  llm: "LLM で判定",
  advisory: "参考（判定しない）",
};

export const TARGET_SECTIONS = ["全体", "Abstract", "Introduction", "Methods", "Results", "Discussion", "図表", "引用"] as const;

export const TARGET_SECTION_LABELS: Record<string, string> = {
  全体: "全体",
  Abstract: "要旨",
  Introduction: "はじめに・関連研究",
  Methods: "手法",
  Results: "結果・実験",
  Discussion: "考察・結論",
  図表: "図表",
  引用: "引用",
};

