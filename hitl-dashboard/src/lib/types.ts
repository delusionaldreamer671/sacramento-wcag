/** TypeScript types matching the Python Pydantic models. */

export type DocumentStatus =
  | "queued"
  | "extracting"
  | "ai_drafting"
  | "hitl_review"
  | "approved"
  | "recompiling"
  | "complete"
  | "failed";

export type WCAGCriterion =
  | "1.1.1"
  | "1.2.1" | "1.2.2" | "1.2.3" | "1.2.4" | "1.2.5"
  | "1.3.1" | "1.3.2" | "1.3.3" | "1.3.4" | "1.3.5"
  | "1.4.1" | "1.4.2" | "1.4.3" | "1.4.4" | "1.4.5" | "1.4.10" | "1.4.11" | "1.4.12" | "1.4.13"
  | "2.1.1" | "2.1.2" | "2.1.4"
  | "2.2.1" | "2.2.2"
  | "2.3.1"
  | "2.4.1" | "2.4.2" | "2.4.3" | "2.4.4" | "2.4.5" | "2.4.6" | "2.4.7"
  | "2.5.1" | "2.5.2" | "2.5.3" | "2.5.4"
  | "3.1.1" | "3.1.2"
  | "3.2.1" | "3.2.2" | "3.2.3" | "3.2.4"
  | "3.3.1" | "3.3.2" | "3.3.3" | "3.3.4"
  | "4.1.1" | "4.1.2" | "4.1.3";

export const WCAG_CRITERION_LABELS: Record<string, string> = {
  "1.1.1": "Non-text Content",
  "1.2.1": "Audio-only and Video-only (Prerecorded)",
  "1.2.2": "Captions (Prerecorded)",
  "1.2.3": "Audio Description or Media Alternative (Prerecorded)",
  "1.2.4": "Captions (Live)",
  "1.2.5": "Audio Description (Prerecorded)",
  "1.3.1": "Info and Relationships",
  "1.3.2": "Meaningful Sequence",
  "1.3.3": "Sensory Characteristics",
  "1.3.4": "Orientation",
  "1.3.5": "Identify Input Purpose",
  "1.4.1": "Use of Color",
  "1.4.2": "Audio Control",
  "1.4.3": "Contrast (Minimum)",
  "1.4.4": "Resize Text",
  "1.4.5": "Images of Text",
  "1.4.10": "Reflow",
  "1.4.11": "Non-text Contrast",
  "1.4.12": "Text Spacing",
  "1.4.13": "Content on Hover or Focus",
  "2.1.1": "Keyboard",
  "2.1.2": "No Keyboard Trap",
  "2.1.4": "Character Key Shortcuts",
  "2.2.1": "Timing Adjustable",
  "2.2.2": "Pause, Stop, Hide",
  "2.3.1": "Three Flashes or Below Threshold",
  "2.4.1": "Bypass Blocks",
  "2.4.2": "Page Titled",
  "2.4.3": "Focus Order",
  "2.4.4": "Link Purpose (In Context)",
  "2.4.5": "Multiple Ways",
  "2.4.6": "Headings and Labels",
  "2.4.7": "Focus Visible",
  "2.5.1": "Pointer Gestures",
  "2.5.2": "Pointer Cancellation",
  "2.5.3": "Label in Name",
  "2.5.4": "Motion Actuation",
  "3.1.1": "Language of Page",
  "3.1.2": "Language of Parts",
  "3.2.1": "On Focus",
  "3.2.2": "On Input",
  "3.2.3": "Consistent Navigation",
  "3.2.4": "Consistent Identification",
  "3.3.1": "Error Identification",
  "3.3.2": "Labels or Instructions",
  "3.3.3": "Error Suggestion",
  "3.3.4": "Error Prevention (Legal, Financial, Data)",
  "4.1.1": "Parsing",
  "4.1.2": "Name, Role, Value",
  "4.1.3": "Status Messages",
};

export type ComplexityFlag = "simple" | "review" | "manual";

export type Severity = "critical" | "serious" | "moderate" | "minor";

export type ReviewerDecision = "approve" | "edit" | "reject";

export interface PDFDocument {
  id: string;
  filename: string;
  gcs_input_path: string;
  gcs_output_path: string | null;
  status: DocumentStatus;
  page_count: number;
  created_at: string;
  updated_at: string;
}

export interface ExtractionResult {
  document_id: string;
  adobe_job_id: string;
  extracted_json_path: string;
  auto_tag_json_path: string;
  elements_count: number;
  images_count: number;
  tables_count: number;
}

export interface WCAGFinding {
  id: string;
  document_id: string;
  element_id: string;
  criterion: WCAGCriterion;
  severity: Severity;
  description: string;
  suggested_fix: string | null;
  ai_draft: string | null;
  complexity: ComplexityFlag;
}

export interface HITLReviewItem {
  id: string;
  document_id: string;
  finding_id: string;
  element_type: string;
  original_content: Record<string, unknown>;
  ai_suggestion: string;
  reviewer_decision: ReviewerDecision | null;
  reviewer_edit: string | null;
  reviewed_at: string | null;
  reviewed_by: string | null;
}

export interface RemediatedDocument {
  document_id: string;
  semantic_html_path: string;
  pdfua_output_path: string;
  axe_score: number | null;
  wcag_violations_remaining: number;
  manual_review_items: number;
}

export interface DocumentUploadResponse {
  document_id: string;
  status: DocumentStatus;
  message: string;
}

export interface DocumentStatusResponse {
  document_id: string;
  filename: string;
  status: DocumentStatus;
  page_count: number;
  created_at: string;
  updated_at: string;
}

export interface ReviewDecisionPayload {
  decision: ReviewerDecision;
  reviewer_edit?: string;
  reviewed_by: string;
}

export interface BatchApproveRequest {
  item_ids: string[];
  reviewed_by: string;
}

export interface PipelineHealthResponse {
  status: string;
  services: Record<string, string>;
}

/** Dashboard-specific aggregate types */

export interface DocumentQueueStats {
  total: number;
  by_status: Record<DocumentStatus, number>;
}

export interface ReviewQueueItem {
  document: PDFDocument;
  pending_reviews: number;
  total_findings: number;
}

// --- Phase C: New types for proposals, rules, audit ---

export type UserRole = "admin" | "reviewer";

export interface User {
  user_id: string;
  role: UserRole;
  token_hash: string;
}

export type ProposalStatus = "pending" | "approved" | "applied" | "rejected" | "rolled_back";

export interface ChangeProposal {
  id: string;
  document_id: string;
  review_item_id: string | null;
  proposed_by: string;
  human_comment: string;
  system_evaluation: SystemEvaluation;
  system_recommendation: "approve" | "reject";
  human_override: number;
  status: ProposalStatus;
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
}

export interface SystemEvaluation {
  compliance_impact: "positive" | "neutral" | "negative";
  risk: "low" | "medium" | "high";
  reversibility: boolean;
  scope: "single_doc" | "global_rule";
  evidence: string;
  recommendation: "approve" | "reject";
  reason: string;
}

export type RuleStatus = "candidate" | "active" | "retired";

export interface Rule {
  id: string;
  trigger_pattern: string;
  action: Record<string, unknown>;
  confidence: number;
  created_from: string | null;
  validated_on_docs: string[];
  rollback_supported: number;
  version: number;
  status: RuleStatus;
  created_at: string;
  updated_at: string;
}

export interface AuditEntry {
  id: number;
  entity_type: string;
  entity_id: string;
  action: string;
  performed_by: string | null;
  old_value: string | null;
  new_value: string | null;
  timestamp: string;
}
