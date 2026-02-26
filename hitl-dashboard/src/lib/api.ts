/**
 * API client for the WCAG Remediation Pipeline backend.
 * Base URL is configured via NEXT_PUBLIC_API_URL environment variable.
 */

import type {
  AuditEntry,
  BatchApproveRequest,
  ChangeProposal,
  DocumentStatusResponse,
  HITLReviewItem,
  PDFDocument,
  PipelineHealthResponse,
  ReviewDecisionPayload,
  Rule,
} from "./types";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

let _authToken: string | null = null;

export function setAuthToken(token: string | null) {
  _authToken = token;
}

export function getAuthToken(): string | null {
  return _authToken;
}

class APIError extends Error {
  constructor(
    public readonly status: number,
    public readonly statusText: string,
    message: string,
  ) {
    super(message);
    this.name = "APIError";
  }
}

/**
 * Safely extract a human-readable error message from a fetch Response.
 * Handles: plain text, JSON { detail: string }, JSON { detail: { message: string } },
 * and falls back to statusText to prevent [object Object] display.
 */
async function extractErrorMessage(response: Response): Promise<string> {
  try {
    const text = await response.text();
    try {
      const json = JSON.parse(text);
      if (typeof json.detail === "string") return json.detail;
      if (json.detail?.message) return json.detail.message;
      if (typeof json.message === "string") return json.message;
      return JSON.stringify(json.detail ?? json);
    } catch {
      return text || response.statusText;
    }
  } catch {
    return response.statusText;
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${BASE_URL}${path}`;

  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(_authToken ? { Authorization: `Bearer ${_authToken}` } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    const detail = await extractErrorMessage(response);
    throw new APIError(
      response.status,
      response.statusText,
      `API request failed: ${response.status} ${detail} (${url})`,
    );
  }

  // 204 No Content — return undefined cast to T
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

/**
 * Fetch the list of documents in the remediation queue.
 *
 * @param skip - Number of records to skip (pagination offset). Defaults to 0.
 * @param limit - Maximum number of records to return. Defaults to 50.
 */
export async function fetchDocuments(
  skip = 0,
  limit = 50,
): Promise<PDFDocument[]> {
  const params = new URLSearchParams({
    skip: String(skip),
    limit: String(limit),
  });
  return request<PDFDocument[]>(`/api/documents?${params.toString()}`);
}

/**
 * Fetch status and metadata for a single document by ID.
 */
export async function fetchDocument(
  id: string,
): Promise<DocumentStatusResponse> {
  if (!id) throw new Error("fetchDocument: id is required");
  return request<DocumentStatusResponse>(`/api/documents/${encodeURIComponent(id)}`);
}

/**
 * Fetch all HITL review items for a given document.
 * Returns items with reviewer_decision === null (pending) first.
 */
export async function fetchReviewItems(
  documentId: string,
): Promise<HITLReviewItem[]> {
  if (!documentId) throw new Error("fetchReviewItems: documentId is required");
  return request<HITLReviewItem[]>(
    `/api/documents/${encodeURIComponent(documentId)}/review-items`,
  );
}

/**
 * Submit a reviewer decision for a single HITL review item.
 *
 * @param itemId - The review item ID to update.
 * @param payload - Decision payload: decision type, optional edit, reviewer identity.
 */
export async function submitReview(
  itemId: string,
  payload: ReviewDecisionPayload,
): Promise<void> {
  if (!itemId) throw new Error("submitReview: itemId is required");
  await request<void>(`/api/review-items/${encodeURIComponent(itemId)}/decision`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/**
 * Batch-approve multiple SIMPLE-flagged review items at once.
 */
export async function batchApprove(request_: BatchApproveRequest): Promise<void> {
  if (!request_.item_ids?.length) {
    throw new Error("batchApprove: item_ids must be a non-empty array");
  }
  await request<void>("/api/review-items/batch-approve", {
    method: "POST",
    body: JSON.stringify(request_),
  });
}

/**
 * Fetch pipeline health status.
 */
export async function fetchHealth(): Promise<PipelineHealthResponse> {
  return request<PipelineHealthResponse>("/api/health");
}

/**
 * Upload a PDF and synchronously convert it to an accessible HTML or PDF/UA document.
 * Returns a Blob that can be downloaded directly via URL.createObjectURL().
 *
 * Note: This may take 30-90 seconds depending on document size and AI processing.
 * The endpoint runs the full pipeline synchronously (Adobe Extract → AI → build output).
 */
export interface ConvertResult {
  blob: Blob;
  taskId: string | null;
}

export async function uploadAndConvert(
  file: File,
  outputFormat: "html" | "pdf" = "html",
): Promise<ConvertResult> {
  if (!file) throw new Error("uploadAndConvert: file is required");

  const formData = new FormData();
  formData.append("file", file);

  const url = `${BASE_URL}/api/convert?output_format=${encodeURIComponent(outputFormat)}`;

  const response = await fetch(url, {
    method: "POST",
    body: formData,
    // Do NOT set Content-Type — browser sets multipart boundary automatically
  });

  if (!response.ok) {
    const detail = await extractErrorMessage(response);
    throw new APIError(
      response.status,
      response.statusText,
      `Conversion failed: ${response.status} ${detail}`,
    );
  }

  const taskId = response.headers.get("X-Task-Id");
  const blob = await response.blob();
  return { blob, taskId };
}

export interface RemediationEvent {
  id: string;
  component: string;
  element_id: string;
  before: unknown;
  after: unknown;
  source: string;
  timestamp: string;
}

export interface RemediationReport {
  task_id: string;
  event_count: number;
  events: RemediationEvent[];
}

export async function fetchRemediationReport(
  taskId: string,
): Promise<RemediationReport> {
  return request<RemediationReport>(`/api/${encodeURIComponent(taskId)}/fixes-applied`);
}

export { APIError };

// --- Analysis API (3-step flow) ---

export interface AnalysisProposal {
  id: string;
  category: string;
  wcag_criterion: string;
  rule_name: string;
  element_type: string;
  element_id: string;
  image_id?: string;
  description: string;
  proposed_fix: string;
  severity: string;
  page: number;
  auto_fixable: boolean;
  action_type: "auto_fix" | "ai_draft" | "manual_review";
}

export interface RuleBreakdownEntry {
  criterion: string;
  name: string;
  status: "pass" | "fail" | "not_applicable" | "error";
  finding_count: number;
  severity_max: string | null;
}

export interface AnalysisSummary {
  total_issues: number;
  critical: number;
  serious: number;
  moderate: number;
  warning: number;
  auto_fixable: number;
  needs_review: number;
  rules_checked: number;
  rules_passed: number;
  rules_failed: number;
  rules_not_applicable: number;
  rules_errored: number;
  coverage_pct: number;
  rules_breakdown: RuleBreakdownEntry[];
}

export interface AltTextProposal {
  id: string;
  image_id: string;
  block_id: string;
  page_num: number;
  original_alt: string;
  proposed_alt: string;
  image_classification: string;
  confidence: number;
  status: string;
  reviewer_decision: string | null;
  reviewer_edit: string | null;
}

export interface AnalysisResult {
  task_id: string;
  filename: string;
  page_count: number;
  proposals: AnalysisProposal[];
  summary: AnalysisSummary;
  alt_text_proposals: AltTextProposal[];
}

/**
 * Upload a PDF and analyze it for WCAG accessibility issues.
 * Returns a list of proposals without applying any remediations.
 */
export async function analyzeDocument(file: File): Promise<AnalysisResult> {
  if (!file) throw new Error("analyzeDocument: file is required");

  const formData = new FormData();
  formData.append("file", file);

  const url = `${BASE_URL}/api/analyze`;

  const response = await fetch(url, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const detail = await extractErrorMessage(response);
    throw new APIError(
      response.status,
      response.statusText,
      `Analysis failed: ${response.status} ${detail}`,
    );
  }

  return response.json() as Promise<AnalysisResult>;
}

/**
 * Upload a PDF and apply remediations, returning the accessible output.
 * Returns a Blob that can be downloaded directly via URL.createObjectURL().
 */
export async function remediateDocument(
  file: File,
  outputFormat: "html" | "pdf" = "html",
  approvedIds?: string[],
): Promise<ConvertResult> {
  if (!file) throw new Error("remediateDocument: file is required");

  const formData = new FormData();
  formData.append("file", file);
  if (approvedIds && approvedIds.length > 0) {
    formData.append("approved_ids", JSON.stringify(approvedIds));
  }

  const url = `${BASE_URL}/api/remediate?output_format=${encodeURIComponent(outputFormat)}&validation_mode=draft`;

  const response = await fetch(url, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const detail = await extractErrorMessage(response);
    throw new APIError(
      response.status,
      response.statusText,
      `Remediation failed: ${response.status} ${detail}`,
    );
  }

  const taskId = response.headers.get("X-Task-Id");
  const blob = await response.blob();
  return { blob, taskId };
}

// --- Proposals API ---

export async function createProposal(data: {
  document_id: string;
  review_item_id?: string;
  human_comment: string;
  element_type?: string;
  finding_severity?: string;
  finding_criterion?: string;
}): Promise<ChangeProposal> {
  return request<ChangeProposal>("/api/proposals", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function fetchProposals(
  documentId?: string,
  status?: string,
): Promise<ChangeProposal[]> {
  const params = new URLSearchParams();
  if (documentId) params.set("document_id", documentId);
  if (status) params.set("status", status);
  const qs = params.toString();
  return request<ChangeProposal[]>(`/api/proposals${qs ? `?${qs}` : ""}`);
}

export async function applyProposal(proposalId: string): Promise<ChangeProposal> {
  return request<ChangeProposal>(`/api/proposals/${encodeURIComponent(proposalId)}/apply`, {
    method: "POST",
  });
}

export async function rollbackProposal(proposalId: string): Promise<ChangeProposal> {
  return request<ChangeProposal>(`/api/proposals/${encodeURIComponent(proposalId)}/rollback`, {
    method: "POST",
  });
}

// --- Rules API ---

export async function fetchRules(status?: string): Promise<Rule[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  const qs = params.toString();
  return request<Rule[]>(`/api/rules${qs ? `?${qs}` : ""}`);
}

export async function createRule(data: {
  trigger_pattern: string;
  action: Record<string, unknown>;
  confidence?: number;
  created_from?: string;
}): Promise<Rule> {
  return request<Rule>("/api/rules", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateRuleStatus(
  ruleId: string,
  status: string,
): Promise<Rule> {
  return request<Rule>(`/api/rules/${encodeURIComponent(ruleId)}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

// --- Audit API ---

export async function fetchAuditTrail(
  entityType: string,
  entityId: string,
): Promise<AuditEntry[]> {
  return request<AuditEntry[]>(
    `/api/audit/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}`,
  );
}

// --- WCAG Rules Reference API ---

export interface TechniqueRef {
  id: string;
  title: string;
  technique_type: string;
  pdf_structure: string;
  check_description: string;
}

export interface FailureTechniqueRef {
  id: string;
  title: string;
  description: string;
  pdf_implication: string;
}

export interface WCAGRuleRef {
  criterion: string;
  name: string;
  level: string;
  principle: string;
  guideline: string;
  description: string;
  pdf_applicability: string;
  automation: string;
  default_severity: string;
  default_remediation: string;
  condition: string;
  pdf_techniques: TechniqueRef[];
  failure_techniques: FailureTechniqueRef[];
}

export async function fetchWCAGRules(): Promise<WCAGRuleRef[]> {
  return request<WCAGRuleRef[]>("/api/wcag-rules");
}

// --- Coverage Matrix API ---

export interface CoverageMatrixEntry {
  criterion: string;
  name: string;
  level: string;
  principle: string;
  guideline: string;
  description: string;
  pdf_applicability: string;
  automation: string;
  default_severity: string;
  default_remediation: string;
  condition: string;
  pdf_techniques: TechniqueRef[];
  failure_techniques: FailureTechniqueRef[];
}

export interface CoverageSummary {
  total_criteria: number;
  by_level: Record<string, number>;
  by_automation: Record<string, number>;
  by_applicability: Record<string, number>;
  by_remediation: Record<string, number>;
}

export interface ContentTypeEntry {
  content_type: string;
  description: string;
  relevant_criteria: string[];
  automated_count: number;
  ai_assisted_count: number;
  human_review_count: number;
  automated_actions: string[];
  ai_assisted_actions: string[];
  human_review_actions: string[];
}

export async function fetchCoverageMatrix(): Promise<CoverageMatrixEntry[]> {
  return request<CoverageMatrixEntry[]>("/api/wcag/coverage-matrix");
}

export async function fetchCoverageSummary(): Promise<CoverageSummary> {
  return request<CoverageSummary>("/api/wcag/coverage-summary");
}

export async function fetchContentTypeMatrix(): Promise<ContentTypeEntry[]> {
  return request<ContentTypeEntry[]>("/api/wcag/content-type-matrix");
}

// --- Alt Text Proposals API ---

export async function fetchAltTextProposals(
  taskId: string,
): Promise<{ task_id: string; proposals: AltTextProposal[]; count: number }> {
  return request<{ task_id: string; proposals: AltTextProposal[]; count: number }>(
    `/api/documents/${encodeURIComponent(taskId)}/alt-text-proposals`,
  );
}

export async function submitAltTextDecision(
  proposalId: string,
  decision: string,
  reviewerEdit?: string,
  reviewedBy?: string,
): Promise<AltTextProposal> {
  return request<AltTextProposal>(
    `/api/alt-text-proposals/${encodeURIComponent(proposalId)}/decision`,
    {
      method: "POST",
      body: JSON.stringify({
        decision,
        reviewer_edit: reviewerEdit ?? null,
        reviewed_by: reviewedBy ?? null,
      }),
    },
  );
}

export async function batchApproveAltText(
  proposalIds: string[],
  reviewedBy?: string,
): Promise<{ approved_count: number }> {
  return request<{ approved_count: number }>(
    "/api/alt-text-proposals/batch-approve",
    {
      method: "POST",
      body: JSON.stringify({
        proposal_ids: proposalIds,
        reviewed_by: reviewedBy ?? null,
      }),
    },
  );
}
