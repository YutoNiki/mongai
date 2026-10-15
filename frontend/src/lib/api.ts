/** core の API（/api）。型は core/api/schemas.py と core/analyze/models.py に対応する。 */

import { applyDemoStatus, demoHiddenFindings } from "./demoStatus";
import type { AnalysisOptions, InstructionDraft, InstructionProfile, InstructionSnapshot, DocumentType, ProfileScope, Requirement, RequirementsResponse } from "./instructions";

export interface BBox {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Finding {
  finding_id: string;
  doc_id: string;
  category: string;
  subcategory: string;
  severity: "critical" | "major" | "suggestion";
  section_id: string | null;
  page: number;
  bboxes: BBox[];
  sentence_ids: string[];
  quote: string;
  issue: string;
  rationale: string;
  evidence: { type: string; ref: string; excerpt: string }[];
  suggestions: string[];
  confidence: "high" | "low";
  votes: number;
  samples: number;
  models: { primary: string; secondary: string | null; agreed: boolean | null };
  status: "open" | "accepted" | "dismissed";
  dismiss_reason: string | null;
}

/** 多数決（複数回の判定）を取った指摘か。1回だけの判定の指摘には、確度と回数を示さない。 */
export function isVoted(finding: Pick<Finding, "samples">): boolean {
  return finding.samples >= 2;
}

export interface Progress {
  stage: string | null;
  done: number;
  total: number;
}

export interface AnalysisRecord {
  analysis_id: string;
  doc_id: string;
  status: "queued" | "running" | "completed" | "failed";
  settings: { output_lang?: string; document_type?: DocumentType; instruction_snapshot?: InstructionSnapshot | null };
  progress: Progress;
  warnings: string[];
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface SectionCount {
  key: string;
  label: string;
  count: number;
}

export interface Counts {
  total: number;
  by_severity: Record<string, number>;
  by_category: Record<string, number>;
  by_section: SectionCount[];
  section_of: Record<string, string>;
  by_status: Record<string, number>;
  by_confidence: Record<string, number>;
}

export interface UsageRow {
  endpoint: string;
  model: string;
  role: string;
  calls: number;
  requests: number;
  retries: number;
  failures: number;
  prompt_tokens: number;
  completion_tokens: number;
  latency_s: number;
}

export interface AnalysisSummary {
  analysis: AnalysisRecord;
  paper: { doc_id: string; filename: string; size: number; uploaded_at: string } | null;
  document: { title: string | null; lang: "ja" | "en"; page_count: number } | null;
  counts: Counts;
  previous: { analysis_id: string; created_at: string; total: number; by_severity: Record<string, number> } | null;
  models: Record<string, string>;
  api_usage: UsageRow[];
  api_requests: number;
  external_requests: number;
  /** デモの論文の解析（削除できない） */
  sample: boolean;
}

/** 前回の解析との差分（GET /analyses/{id}/diff） */
export interface AnalysisDiff {
  previous: AnalysisSummary["previous"];
  resolved: Finding[];
  new: string[];
  persisting: { finding_id: string; previous_finding_id: string }[];
}

export interface AnalysisListItem {
  analysis_id: string;
  doc_id: string;
  title: string | null;
  filename: string | null;
  status: AnalysisRecord["status"];
  created_at: string;
  finished_at: string | null;
  total: number;
  progress: Progress;
  /** デモの論文（削除できない） */
  sample: boolean;
}

export interface FindingFilters {
  category?: string;
  severity?: string;
  section_id?: string;
  confidence?: string;
  status?: string;
}

export interface ExternalRequest {
  kind: string;
  doi?: string | null;
  title?: string | null;
  first_author?: string | null;
  year?: number | null;
  term?: string;
  lang?: string;
}

export interface AuditEntry {
  seq: number;
  ts: string;
  request_id: string;
  analysis_id: string | null;
  doc_id: string | null;
  kind: string | null;
  request: ExternalRequest | string;
  decision: string;
  reason: string | null;
}

/** 解析で照会した未知の用語と、その結果（GET /analyses/{id}/terms）。description は学外の辞書の説明 */
export interface TermLookup {
  term: string;
  /** 実際に照会した語（日本語の論文では英語の対応語） */
  query: string | null;
  request_id: string | null;
  status: "pending" | "approved" | "rejected" | "found" | "blocked" | "failed";
  /** 学外の辞書で該当した見出し */
  matched: string | null;
  description: string | null;
  source: string | null;
  reason: string | null;
  updated_at: string;
}

export interface QueueItem {
  request_id: string;
  analysis_id: string;
  doc_id: string;
  kind: string;
  request: ExternalRequest;
  status: string;
  created_at: string;
}

export interface DailyUsage {
  day: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
}

/** ルール（SPEC 3.2）。型は core/rules/models.py に対応する。 */
export type RuleStatus = "pending" | "approved" | "disabled";
export interface Rule {
  rule_id: string;
  target_section: string;
  rule: string;
  check_type: "mechanical" | "llm" | "advisory";
  checker: string | null;
  lang: "ja" | "en" | "both";
  source: { doc: string; page: number | null };
  merged_sources: { doc: string; page: number | null }[];
  field_tags: string[];
  status: RuleStatus;
  builtin: boolean;
  document_id: string | null;
  updated_at: string;
}

export interface RuleDocument {
  document_id: string;
  filename: string;
  kind: "pdf" | "markdown" | "text";
  status: "queued" | "running" | "completed" | "failed";
  rule_count: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export type RuleUpdate = Partial<Pick<Rule, "status" | "rule" | "target_section" | "check_type">>;

/** 専門知識（SPEC 3.1〜3.3）。型は core/lab/models.py に対応する。 */
export interface LabDocument {
  document_id: string;
  filename: string;
  kind: "pdf" | "docx" | "markdown" | "text";
  status: "queued" | "running" | "completed" | "failed";
  comment_count: number;
  rule_count: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface LabComment {
  comment_id: string;
  document_id: string;
  comment: string;
  example: string | null;
  category: "logic" | "readability" | "rule" | "citation" | "other";
  source: { doc: string; page: number | null };
  created_at: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null = null,
  ) {
    super(message);
  }
}

/** ログインの状態（GET /api/me。表示に要る最小限の情報だけ） */
export interface Me {
  authenticated: boolean;
  email: string | null;
  display_name: string | null;
}

/** API がセッションの失効（invalid_session）を返したときに window に送るイベント（lib/auth.tsx が受け取る） */
export const SESSION_INVALID_EVENT = "mongai:session-invalid";

// 401・503 の理由（core/api/dependencies.py と docker/web/auth/*/server.conf）
const AUTH_MESSAGES: Record<string, string> = {
  login_required: "ログインが必要です（ログインしていない場合は、共通デモの閲覧だけができます）。画面右上の「Googleでログイン」からログインしてください",
  invalid_session: "ログインの有効期限が切れたか、ログインの情報が正しくありません。もう一度ログインしてください",
  auth_unavailable: "認証サービスに接続できません。しばらくしてからお試しください",
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { credentials: "same-origin", ...init });
  if (!response.ok) {
    let detail = `${response.status}`;
    let code: string | null = null;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") {
        code = body.detail;
        detail = AUTH_MESSAGES[body.detail] ?? body.detail;
      } else if (Array.isArray(body.detail)) {
        detail = body.detail.map((item: { msg?: string }) => item.msg ?? "入力内容を確認してください").join("、");
      }
    } catch {
      // 本文が JSON でない場合はステータスコードだけを表示する
    }
    if (response.status === 401 && code === "invalid_session") window.dispatchEvent(new Event(SESSION_INVALID_EVENT));
    throw new ApiError(detail, response.status, code);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function query(filters: FindingFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  const text = params.toString();
  return text ? `?${text}` : "";
}

export const api = {
  me: () => request<Me>("/me"),
  async logout(): Promise<void> {
    // 同じオリジンからの POST だけを受け付ける（authn が Origin を確かめる）
    const response = await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
    // 503：サーバー側でセッションを消せなかった（Cookie だけ消えても、写しの Cookie が使えるままになる）
    if (response.status === 503)
      throw new ApiError("ログアウトを確定できませんでした。もう一度お試しください", response.status);
    if (!response.ok) throw new ApiError(`ログアウトできませんでした（${response.status}）`, response.status);
  },
  upload(file: File) {
    const body = new FormData();
    body.append("file", file);
    return request<{ doc_id: string; filename: string; size: number }>("/documents", {
      method: "POST",
      body,
    });
  },
  startAnalysis(docId: string, outputLang: "paper" | "ja", options: AnalysisOptions = {}) {
    return request<AnalysisSummary>("/analyses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_id: docId, output_lang: outputLang, ...options }),
    });
  },
  deleteDocument: (docId: string) =>
    request<undefined>(`/documents/${encodeURIComponent(docId)}`, { method: "DELETE" }),
  usageToday: () => request<DailyUsage>("/usage/today"),
  instructionProfiles: () => request<InstructionProfile[]>("/instruction-profiles"),
  createInstructionDraft: (documentType: DocumentType, rawText: string, profiles: InstructionProfile[]) =>
    request<InstructionDraft>("/instruction-drafts", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_type: documentType, raw_text: rawText,
        profiles: profiles.map((p) => ({ profile_id: p.profile_id, revision: p.revision })) }),
    }),
  instructionDraft: (id: string) => request<InstructionDraft>(`/instruction-drafts/${encodeURIComponent(id)}`),
  editInstructionDraft: (draft: InstructionDraft, requirements: Requirement[], excluded: string[]) =>
    request<InstructionDraft>(`/instruction-drafts/${encodeURIComponent(draft.draft_id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ revision: draft.revision, requirements, excluded_rule_ids: excluded }),
    }),
  confirmInstructionDraft: (draft: InstructionDraft) =>
    request<InstructionDraft>(`/instruction-drafts/${encodeURIComponent(draft.draft_id)}/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ revision: draft.revision }),
    }),
  saveInstructionProfile: (draft: InstructionDraft, name: string, scope: ProfileScope, previous?: InstructionProfile) =>
    request<InstructionProfile>(`/instruction-profiles${previous ? `/${encodeURIComponent(previous.profile_id)}` : ""}`, {
      method: previous ? "PATCH" : "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, scope, draft_id: draft.draft_id, draft_revision: draft.revision,
        revision: previous?.revision }),
    }),
  deleteInstructionProfile: (id: string) => request<undefined>(`/instruction-profiles/${encodeURIComponent(id)}`, { method: "DELETE" }),
  requirements: (id: string) => request<RequirementsResponse>(`/analyses/${encodeURIComponent(id)}/requirements`),
  requirementsUrl: (id: string, format: "csv" | "json") => `/api/analyses/${encodeURIComponent(id)}/requirements.${format}`,
  listAnalyses: () => request<AnalysisListItem[]>("/analyses"),
  summary: (id: string) => request<AnalysisSummary>(`/analyses/${encodeURIComponent(id)}`),
  diff: (id: string) => request<AnalysisDiff>(`/analyses/${encodeURIComponent(id)}/diff`),
  // デモの解析は、このタブで変えたステータスを重ねる（サーバーには保存しない。lib/demoStatus.ts）
  findings: (id: string, filters: FindingFilters = {}) =>
    request<Finding[]>(`/analyses/${encodeURIComponent(id)}/findings${query(filters)}`).then((list) =>
      applyDemoStatus(id, list),
    ),
  updateStatus(id: string, findingId: string, status: Finding["status"], reason?: string) {
    return request<Finding>(
      `/analyses/${encodeURIComponent(id)}/findings/${encodeURIComponent(findingId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, reason: reason ?? null }),
      },
    );
  },
  auditLog: (analysisId?: string) =>
    request<{ available: boolean; mode: string | null; entries: AuditEntry[] }>(
      `/audit-log${analysisId ? `?analysis_id=${encodeURIComponent(analysisId)}` : ""}`,
    ),
  queue: () => request<QueueItem[]>("/gateway/queue"),
  terms: (analysisId: string) => request<TermLookup[]>(`/analyses/${encodeURIComponent(analysisId)}/terms`),
  decide: (requestId: string, action: "approve" | "reject") =>
    request<{ request_id: string; decision: string; reason: string | null }>(
      `/gateway/queue/${encodeURIComponent(requestId)}/${action}`,
      { method: "POST" },
    ),
  rules: () => request<Rule[]>("/rules"),
  updateRule: (ruleId: string, update: RuleUpdate) =>
    request<Rule>(`/rules/${encodeURIComponent(ruleId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }),
  bulkRuleStatus: (ruleIds: string[], status: RuleStatus) =>
    request<Rule[]>("/rules/bulk-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rule_ids: ruleIds, status }),
    }),
  ruleDocuments: () => request<RuleDocument[]>("/rule-documents"),
  uploadRuleDocument(file: File) {
    const body = new FormData();
    body.append("file", file);
    return request<RuleDocument>("/rule-documents", { method: "POST", body });
  },
  deleteRuleDocument: (documentId: string) =>
    request<undefined>(`/rule-documents/${encodeURIComponent(documentId)}`, { method: "DELETE" }),
  labDocuments: () => request<LabDocument[]>("/lab/documents"),
  uploadLabDocument(file: File) {
    const body = new FormData();
    body.append("file", file);
    return request<LabDocument>("/lab/documents", { method: "POST", body });
  },
  deleteLabDocument: (documentId: string) =>
    request<undefined>(`/lab/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" }),
  labComments: () => request<LabComment[]>("/lab/comments"),
  deleteLabComment: (commentId: string) =>
    request<undefined>(`/lab/comments/${encodeURIComponent(commentId)}`, { method: "DELETE" }),
  csvUrl: (id: string, filters: FindingFilters = {}) =>
    `/api/analyses/${encodeURIComponent(id)}/findings.csv${query(filters)}`,
  jsonUrl: (id: string) => `/api/analyses/${encodeURIComponent(id)}/findings.json`,
  reportUrl: (id: string) => `/api/analyses/${encodeURIComponent(id)}/report.txt`,
  // デモの解析で、このタブの中だけで却下・対応済みにした指摘は書き込まない
  annotatedUrl: (id: string) => {
    const hide = demoHiddenFindings(id);
    const params = hide.length ? `?${new URLSearchParams(hide.map((h) => ["hide", h])).toString()}` : "";
    return `/api/analyses/${encodeURIComponent(id)}/annotated.pdf${params}`;
  },
  paperUrl: (docId: string) => `/api/documents/${encodeURIComponent(docId)}/paper.pdf`,
};
