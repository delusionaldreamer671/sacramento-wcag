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
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore parse error — use statusText fallback
    }
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
  return request<PipelineHealthResponse>("/health");
}

/**
 * Upload a PDF and synchronously convert it to an accessible HTML or PDF/UA document.
 * Returns a Blob that can be downloaded directly via URL.createObjectURL().
 *
 * Note: This may take 30-90 seconds depending on document size and AI processing.
 * The endpoint runs the full pipeline synchronously (Adobe Extract → AI → build output).
 */
export async function uploadAndConvert(
  file: File,
  outputFormat: "html" | "pdf" = "html",
): Promise<Blob> {
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
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore parse error — use statusText fallback
    }
    throw new APIError(
      response.status,
      response.statusText,
      `Conversion failed: ${response.status} ${detail}`,
    );
  }

  return response.blob();
}

export { APIError };

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
