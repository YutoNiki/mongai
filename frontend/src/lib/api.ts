/** core の API（/api）。型は core/api/schemas.py と core/analyze/models.py に対応する。 */

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

export interface Progress {
  stage: string | null;
  done: number;
  total: number;
}

export interface AnalysisRecord {
  analysis_id: string;
  doc_id: string;
  status: "queued" | "running" | "completed" | "failed";
  settings: { output_lang?: string };
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

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // 本文が JSON でない場合はステータスコードだけを表示する
    }
    throw new ApiError(detail);
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
  upload(file: File) {
    const body = new FormData();
    body.append("file", file);
    return request<{ doc_id: string; filename: string; size: number }>("/documents", {
      method: "POST",
      body,
    });
  },
  startAnalysis(docId: string, outputLang: "paper" | "ja") {
    return request<AnalysisSummary>("/analyses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_id: docId, output_lang: outputLang }),
    });
  },
  deleteDocument: (docId: string) =>
    request<undefined>(`/documents/${encodeURIComponent(docId)}`, { method: "DELETE" }),
  usageToday: () => request<DailyUsage>("/usage/today"),
  listAnalyses: () => request<AnalysisListItem[]>("/analyses"),
  summary: (id: string) => request<AnalysisSummary>(`/analyses/${encodeURIComponent(id)}`),
  diff: (id: string) => request<AnalysisDiff>(`/analyses/${encodeURIComponent(id)}/diff`),
  findings: (id: string, filters: FindingFilters = {}) =>
    request<Finding[]>(`/analyses/${encodeURIComponent(id)}/findings${query(filters)}`),
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
  csvUrl: (id: string, filters: FindingFilters = {}) =>
    `/api/analyses/${encodeURIComponent(id)}/findings.csv${query(filters)}`,
  jsonUrl: (id: string) => `/api/analyses/${encodeURIComponent(id)}/findings.json`,
  reportUrl: (id: string) => `/api/analyses/${encodeURIComponent(id)}/report.txt`,
  annotatedUrl: (id: string) => `/api/analyses/${encodeURIComponent(id)}/annotated.pdf`,
  paperUrl: (docId: string) => `/api/documents/${encodeURIComponent(docId)}/paper.pdf`,
};
