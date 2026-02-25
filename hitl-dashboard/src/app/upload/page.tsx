"use client";

import { useState, useRef, useCallback } from "react";
import {
  Upload,
  FileText,
  Download,
  AlertCircle,
  Loader2,
  X,
  ChevronDown,
  ChevronRight,
  Shield,
  CheckCircle2,
  Search,
  ImageOff,
  Heading,
  Table2,
  Languages,
  ArrowDownUp,
  Wrench,
  Eye,
  Check,
  ThumbsUp,
  ThumbsDown,
  MessageSquare,
  Send,
} from "lucide-react";
import {
  analyzeDocument,
  remediateDocument,
  fetchRemediationReport,
  createProposal,
} from "@/lib/api";
import type {
  AnalysisResult,
  AnalysisProposal,
  RemediationReport,
  RemediationEvent,
} from "@/lib/api";

type FlowState = "idle" | "analyzing" | "proposals" | "remediating" | "done" | "error";
type OutputFormat = "html" | "pdf";

// ---------------------------------------------------------------------------
// Helpers for the remediation report (unchanged)
// ---------------------------------------------------------------------------

/** Group events by component name for display. */
function groupByComponent(events: RemediationEvent[]): Record<string, RemediationEvent[]> {
  const groups: Record<string, RemediationEvent[]> = {};
  for (const evt of events) {
    const key = evt.component;
    if (!groups[key]) groups[key] = [];
    groups[key].push(evt);
  }
  return groups;
}

/** Human-readable label for a component enum value. */
function componentLabel(component: string): string {
  const labels: Record<string, string> = {
    ALT_TEXT: "Alt Text Added",
    HEADING_HIERARCHY: "Heading Hierarchy Fixed",
    TABLE_STRUCTURE: "Table Structure Corrected",
    FIGURE_CAPTION: "Figure Caption Associated",
    LANGUAGE_TAG: "Language Tag Set",
    MARK_INFO: "Mark Info Added",
    PDFUA_METADATA: "PDF/UA Metadata Injected",
    VIEWER_PREFERENCES: "Viewer Preferences Set",
    TAB_ORDER: "Tab Order Fixed",
    CIDSET_REMOVAL: "CIDSet Removed",
  };
  return labels[component] || component.replace(/_/g, " ");
}

/** Source badge color. */
function sourceBadge(source: string): string {
  switch (source) {
    case "pipeline": return "bg-blue-100 text-blue-800";
    case "ai": return "bg-purple-100 text-purple-800";
    case "clause_fixer": return "bg-amber-100 text-amber-800";
    case "human": return "bg-green-100 text-green-800";
    default: return "bg-gray-100 text-gray-800";
  }
}

// ---------------------------------------------------------------------------
// Category icon and label helpers
// ---------------------------------------------------------------------------

function categoryIcon(category: string) {
  switch (category) {
    case "alt_text":
      return <ImageOff className="h-4 w-4" aria-hidden="true" />;
    case "heading_hierarchy":
      return <Heading className="h-4 w-4" aria-hidden="true" />;
    case "table_structure":
      return <Table2 className="h-4 w-4" aria-hidden="true" />;
    case "language":
      return <Languages className="h-4 w-4" aria-hidden="true" />;
    case "reading_order":
      return <ArrowDownUp className="h-4 w-4" aria-hidden="true" />;
    default:
      return <Wrench className="h-4 w-4" aria-hidden="true" />;
  }
}

function categoryLabel(category: string): string {
  const labels: Record<string, string> = {
    alt_text: "Missing Alt Text",
    heading_hierarchy: "Heading Issue",
    table_structure: "Table Structure",
    language: "Language Tag",
    reading_order: "Reading Order",
  };
  return labels[category] || category.replace(/_/g, " ");
}

function severityColor(severity: string): string {
  switch (severity) {
    case "critical": return "bg-red-100 text-red-800 border-red-200";
    case "serious": return "bg-orange-100 text-orange-800 border-orange-200";
    case "moderate": return "bg-yellow-100 text-yellow-800 border-yellow-200";
    case "minor": return "bg-blue-100 text-blue-800 border-blue-200";
    default: return "bg-gray-100 text-gray-800 border-gray-200";
  }
}

// ---------------------------------------------------------------------------
// Post-remediation review
// ---------------------------------------------------------------------------

type ReviewStatus = "looks_good" | "needs_attention" | "unreviewed";

interface PostRemediationReviewProps {
  events: RemediationEvent[];
  documentId: string | null;
}

function PostRemediationReview({ events, documentId }: PostRemediationReviewProps) {
  const [statuses, setStatuses] = useState<Record<string, ReviewStatus>>(
    () => Object.fromEntries(events.map((e) => [e.id, "unreviewed" as ReviewStatus])),
  );
  const [comments, setComments] = useState<Record<string, string>>({});
  const [expandedComments, setExpandedComments] = useState<Set<string>>(new Set());
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [summary, setSummary] = useState<{ approved: number; flagged: number } | null>(null);

  const toggleStatus = (id: string, status: ReviewStatus) => {
    setStatuses((prev) => ({ ...prev, [id]: status }));
    if (status === "needs_attention") {
      setExpandedComments((prev) => {
        const next = new Set(prev);
        next.add(id);
        return next;
      });
    } else {
      setExpandedComments((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const toggleComment = (id: string) => {
    setExpandedComments((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSubmitReview = async () => {
    setSubmitting(true);
    setSubmitError(null);

    let approved = 0;
    let flagged = 0;

    try {
      for (const event of events) {
        const status = statuses[event.id] ?? "unreviewed";
        if (status === "needs_attention") {
          const comment = (comments[event.id] ?? "").trim();
          if (documentId) {
            await createProposal({
              document_id: documentId,
              human_comment: comment
                ? `Post-remediation flag on element ${event.element_id}: ${comment}`
                : `Post-remediation flag on element ${event.element_id} (${componentLabel(event.component)})`,
              element_type: event.component,
            });
          }
          flagged += 1;
        } else {
          approved += 1;
        }
      }
      setSummary({ approved, flagged });
      setSubmitted(true);
    } catch (err) {
      setSubmitError(
        err instanceof Error ? err.message : "Failed to submit review. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted && summary) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="mt-4 rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm"
      >
        <p className="font-medium text-green-800">Review submitted</p>
        <p className="mt-0.5 text-green-700">
          {summary.approved} item{summary.approved !== 1 ? "s" : ""} approved
          {summary.flagged > 0 && (
            <>, {summary.flagged} item{summary.flagged !== 1 ? "s" : ""} flagged for follow-up</>
          )}
          .
        </p>
      </div>
    );
  }

  return (
    <section
      aria-labelledby="post-review-heading"
      className="mt-6 rounded-lg border border-border bg-card"
    >
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-border bg-muted/30 px-4 py-3">
        <Eye className="h-5 w-5 text-sac-navy" aria-hidden="true" />
        <div className="flex-1">
          <h3 id="post-review-heading" className="text-sm font-semibold text-sac-navy">
            Post-Remediation Review
          </h3>
          <p className="text-xs text-muted-foreground">
            Review each change made by the system. Flag anything that needs attention.
          </p>
        </div>
      </div>

      {events.length === 0 ? (
        <p className="px-4 py-6 text-center text-sm text-muted-foreground">
          No remediation events to review.
        </p>
      ) : (
        <>
          <ul className="divide-y divide-border" aria-label="Remediation events to review">
            {events.map((evt) => {
              const status = statuses[evt.id] ?? "unreviewed";
              const isExpanded = expandedComments.has(evt.id);
              const beforeText = evt.before ? String(evt.before) : "(empty)";
              const afterText = evt.after ? String(evt.after) : "(empty)";

              return (
                <li key={evt.id} className="px-4 py-3 space-y-2">
                  {/* Element info row */}
                  <div className="flex items-start gap-2 flex-wrap">
                    <span className="text-sac-navy mt-0.5">{categoryIcon(evt.component)}</span>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-semibold text-sac-navy uppercase tracking-wide">
                        {componentLabel(evt.component)}
                      </p>
                      {evt.element_id && (
                        <p className="text-[10px] font-mono text-muted-foreground truncate">
                          {String(evt.element_id).substring(0, 60)}
                        </p>
                      )}
                    </div>
                    <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${sourceBadge(evt.source)}`}>
                      {evt.source}
                    </span>
                  </div>

                  {/* Before → After */}
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="rounded bg-red-50 border border-red-100 px-2 py-1.5">
                      <p className="font-medium text-red-700 mb-0.5">Before</p>
                      <p className="text-red-600 truncate">{beforeText.substring(0, 80)}</p>
                    </div>
                    <div className="rounded bg-green-50 border border-green-100 px-2 py-1.5">
                      <p className="font-medium text-green-700 mb-0.5">After</p>
                      <p className="text-green-600 truncate">{afterText.substring(0, 80)}</p>
                    </div>
                  </div>

                  {/* Looks Good / Needs Attention toggle */}
                  <div
                    className="flex items-center gap-2"
                    role="group"
                    aria-label={`Review decision for ${componentLabel(evt.component)} change`}
                  >
                    <button
                      type="button"
                      onClick={() => toggleStatus(evt.id, "looks_good")}
                      aria-pressed={status === "looks_good"}
                      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                        status === "looks_good"
                          ? "border-green-500 bg-green-100 text-green-800"
                          : "border-border bg-background text-muted-foreground hover:border-green-400 hover:text-green-700"
                      }`}
                    >
                      <ThumbsUp className="h-3 w-3" aria-hidden="true" />
                      Looks Good
                    </button>
                    <button
                      type="button"
                      onClick={() => toggleStatus(evt.id, "needs_attention")}
                      aria-pressed={status === "needs_attention"}
                      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                        status === "needs_attention"
                          ? "border-amber-500 bg-amber-100 text-amber-800"
                          : "border-border bg-background text-muted-foreground hover:border-amber-400 hover:text-amber-700"
                      }`}
                    >
                      <ThumbsDown className="h-3 w-3" aria-hidden="true" />
                      Needs Attention
                    </button>
                    {status !== "needs_attention" && (
                      <button
                        type="button"
                        onClick={() => toggleComment(evt.id)}
                        aria-expanded={isExpanded}
                        aria-label={isExpanded ? "Hide comment field" : "Add a comment"}
                        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <MessageSquare className="h-3 w-3" aria-hidden="true" />
                        Comment
                      </button>
                    )}
                  </div>

                  {/* Comment field — shown when expanded */}
                  {isExpanded && (
                    <div className="mt-1">
                      <label className="block">
                        <span className="sr-only">Comment for {componentLabel(evt.component)} change</span>
                        <textarea
                          value={comments[evt.id] ?? ""}
                          onChange={(e) =>
                            setComments((prev) => ({ ...prev, [evt.id]: e.target.value }))
                          }
                          placeholder={
                            status === "needs_attention"
                              ? "Describe what needs attention..."
                              : "Optional comment..."
                          }
                          rows={2}
                          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        />
                      </label>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>

          {/* Submit button */}
          <div className="border-t border-border px-4 py-3 flex items-center justify-between gap-3 flex-wrap">
            {submitError && (
              <p role="alert" className="text-xs text-destructive flex-1">
                {submitError}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {Object.values(statuses).filter((s) => s === "looks_good").length} approved
              {" · "}
              {Object.values(statuses).filter((s) => s === "needs_attention").length} flagged
              {" · "}
              {Object.values(statuses).filter((s) => s === "unreviewed").length} unreviewed
            </p>
            <button
              type="button"
              onClick={handleSubmitReview}
              disabled={submitting}
              className="inline-flex items-center gap-2 rounded-md bg-sac-navy px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-sac-navy/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Send className="h-4 w-4" aria-hidden="true" />
              )}
              {submitting ? "Submitting..." : "Submit Review"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Step indicator
// ---------------------------------------------------------------------------

function StepIndicator({ currentStep }: { currentStep: number }) {
  const steps = [
    { number: 1, label: "Upload & Analyze" },
    { number: 2, label: "Review Proposals" },
    { number: 3, label: "Remediate & Download" },
  ];

  return (
    <nav aria-label="Remediation progress" className="mb-6">
      <ol className="flex items-center gap-2">
        {steps.map((step, idx) => {
          const isActive = step.number === currentStep;
          const isComplete = step.number < currentStep;
          return (
            <li key={step.number} className="flex items-center gap-2">
              {idx > 0 && (
                <div
                  className={`h-px w-6 sm:w-10 ${
                    isComplete ? "bg-sac-navy" : "bg-border"
                  }`}
                  aria-hidden="true"
                />
              )}
              <div className="flex items-center gap-2">
                <span
                  className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${
                    isComplete
                      ? "bg-sac-navy text-white"
                      : isActive
                        ? "border-2 border-sac-navy bg-white text-sac-navy"
                        : "border-2 border-border bg-white text-muted-foreground"
                  }`}
                  aria-current={isActive ? "step" : undefined}
                >
                  {isComplete ? (
                    <Check className="h-3.5 w-3.5" aria-hidden="true" />
                  ) : (
                    step.number
                  )}
                </span>
                <span
                  className={`hidden text-xs font-medium sm:inline ${
                    isActive ? "text-sac-navy" : "text-muted-foreground"
                  }`}
                >
                  {step.label}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Remediation report (unchanged from original)
// ---------------------------------------------------------------------------

function RemediationSummary({ report }: { report: RemediationReport }) {
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const grouped = groupByComponent(report.events);
  const componentKeys = Object.keys(grouped).sort();

  const toggleGroup = (key: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="mt-6 rounded-lg border border-sac-navy/20 bg-white shadow-sac">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-sac-navy/10 bg-sac-light px-4 py-3">
        <Shield className="h-5 w-5 text-sac-navy" aria-hidden="true" />
        <div className="flex-1">
          <h3 className="text-sm font-semibold text-sac-navy">
            Remediation Report
          </h3>
          <p className="text-xs text-muted-foreground">
            {report.event_count} change{report.event_count !== 1 ? "s" : ""} applied to make your document WCAG 2.1 AA compliant
          </p>
        </div>
        <span className="rounded-full bg-sac-navy px-2.5 py-0.5 text-xs font-bold text-white">
          {report.event_count}
        </span>
      </div>

      {/* Component groups */}
      <div className="divide-y divide-border">
        {componentKeys.map(key => {
          const events = grouped[key];
          const isExpanded = expandedGroups.has(key);
          return (
            <div key={key}>
              <button
                type="button"
                onClick={() => toggleGroup(key)}
                className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/50 transition-colors"
                aria-expanded={isExpanded}
              >
                {isExpanded
                  ? <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  : <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                }
                <CheckCircle2 className="h-4 w-4 text-green-600" aria-hidden="true" />
                <span className="flex-1 text-sm font-medium text-foreground">
                  {componentLabel(key)}
                </span>
                <span className="rounded-md bg-secondary px-2 py-0.5 text-xs font-medium text-secondary-foreground">
                  {events.length} fix{events.length !== 1 ? "es" : ""}
                </span>
              </button>

              {isExpanded && (
                <div className="border-t border-border bg-muted/30 px-4 py-2">
                  <table className="w-full text-xs" aria-label={`${componentLabel(key)} details`}>
                    <thead>
                      <tr className="text-left text-muted-foreground">
                        <th className="pb-1 pr-3 font-medium">Element</th>
                        <th className="pb-1 pr-3 font-medium">Before</th>
                        <th className="pb-1 pr-3 font-medium">After</th>
                        <th className="pb-1 font-medium">Source</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/50">
                      {events.map(evt => (
                        <tr key={evt.id}>
                          <td className="py-1.5 pr-3 font-mono text-muted-foreground">
                            {evt.element_id ? String(evt.element_id).substring(0, 40) : "\u2014"}
                          </td>
                          <td className="py-1.5 pr-3 max-w-[200px] truncate text-red-700">
                            {evt.before ? String(evt.before).substring(0, 60) : "(empty)"}
                          </td>
                          <td className="py-1.5 pr-3 max-w-[200px] truncate text-green-700">
                            {evt.after ? String(evt.after).substring(0, 60) : "(empty)"}
                          </td>
                          <td className="py-1.5">
                            <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${sourceBadge(evt.source)}`}>
                              {evt.source}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {report.event_count === 0 && (
        <div className="px-4 py-6 text-center text-sm text-muted-foreground">
          No remediation changes were needed — document was already compliant.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Proposals list
// ---------------------------------------------------------------------------

function ProposalsList({
  proposals,
  selectedIds,
  onToggle,
  onToggleAll,
}: {
  proposals: AnalysisProposal[];
  selectedIds: Set<string>;
  onToggle: (id: string) => void;
  onToggleAll: () => void;
}) {
  const allSelected = proposals.length > 0 && selectedIds.size === proposals.length;
  const someSelected = selectedIds.size > 0 && selectedIds.size < proposals.length;

  return (
    <div
      className="rounded-lg border border-border bg-white shadow-sac"
      role="region"
      aria-label="Accessibility proposals"
    >
      {/* Header with Select All */}
      <div className="flex items-center gap-3 border-b border-border bg-muted/30 px-4 py-3">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={allSelected}
            ref={(el) => {
              if (el) el.indeterminate = someSelected;
            }}
            onChange={onToggleAll}
            className="h-4 w-4 rounded border-border text-sac-navy focus:ring-sac-navy"
            aria-label={allSelected ? "Deselect all proposals" : "Select all proposals"}
          />
          <span className="text-sm font-medium text-foreground">
            {allSelected ? "Deselect All" : "Select All"}
          </span>
        </label>
        <span className="ml-auto text-xs text-muted-foreground">
          {selectedIds.size} of {proposals.length} selected
        </span>
      </div>

      {/* Proposals */}
      <ul className="divide-y divide-border" role="list">
        {proposals.map((proposal) => {
          const isSelected = selectedIds.has(proposal.id);
          return (
            <li key={proposal.id}>
              <label
                className={`flex items-start gap-3 px-4 py-3 cursor-pointer transition-colors ${
                  isSelected ? "bg-sac-light/50" : "hover:bg-muted/30"
                }`}
              >
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => onToggle(proposal.id)}
                  className="mt-0.5 h-4 w-4 rounded border-border text-sac-navy focus:ring-sac-navy"
                  aria-label={`${isSelected ? "Deselect" : "Select"}: ${proposal.description}`}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sac-navy">
                      {categoryIcon(proposal.category)}
                    </span>
                    <span className="text-xs font-semibold text-sac-navy uppercase tracking-wide">
                      {categoryLabel(proposal.category)}
                    </span>
                    <span
                      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${severityColor(proposal.severity)}`}
                    >
                      {proposal.severity}
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      WCAG {proposal.wcag_criterion}
                    </span>
                    {proposal.auto_fixable && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-green-50 border border-green-200 px-2 py-0.5 text-[10px] font-medium text-green-700">
                        <Wrench className="h-2.5 w-2.5" aria-hidden="true" />
                        Auto-fix
                      </span>
                    )}
                    {!proposal.auto_fixable && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                        <Eye className="h-2.5 w-2.5" aria-hidden="true" />
                        Needs review
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-foreground">
                    {proposal.description}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {proposal.proposed_fix}
                  </p>
                  {proposal.page > 0 && (
                    <p className="mt-0.5 text-[10px] text-muted-foreground">
                      Page {proposal.page}
                    </p>
                  )}
                </div>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Analysis summary bar
// ---------------------------------------------------------------------------

function AnalysisSummaryBar({ summary }: { summary: AnalysisResult["summary"] }) {
  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-lg border border-sac-navy/20 bg-sac-light px-4 py-3"
      role="status"
      aria-label="Analysis summary"
    >
      <div className="flex items-center gap-2">
        <Search className="h-4 w-4 text-sac-navy" aria-hidden="true" />
        <span className="text-sm font-semibold text-sac-navy">
          {summary.total_issues} issue{summary.total_issues !== 1 ? "s" : ""} found
        </span>
      </div>
      <span className="hidden sm:inline text-border">|</span>
      <div className="flex flex-wrap gap-2 text-xs">
        {summary.critical > 0 && (
          <span className="rounded-full bg-red-100 px-2 py-0.5 font-medium text-red-800">
            {summary.critical} critical
          </span>
        )}
        {summary.serious > 0 && (
          <span className="rounded-full bg-orange-100 px-2 py-0.5 font-medium text-orange-800">
            {summary.serious} serious
          </span>
        )}
        {summary.moderate > 0 && (
          <span className="rounded-full bg-yellow-100 px-2 py-0.5 font-medium text-yellow-800">
            {summary.moderate} moderate
          </span>
        )}
      </div>
      <span className="hidden sm:inline text-border">|</span>
      <div className="flex gap-2 text-xs">
        <span className="rounded-full bg-green-50 px-2 py-0.5 font-medium text-green-700">
          {summary.auto_fixable} auto-fixable
        </span>
        {summary.needs_review > 0 && (
          <span className="rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-700">
            {summary.needs_review} need review
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [format, setFormat] = useState<OutputFormat>("html");
  const [state, setState] = useState<FlowState>("idle");
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null);
  const [selectedProposalIds, setSelectedProposalIds] = useState<Set<string>>(new Set());
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [downloadName, setDownloadName] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [report, setReport] = useState<RemediationReport | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Determine which step we are on
  const currentStep =
    state === "idle" || state === "analyzing"
      ? 1
      : state === "proposals"
        ? 2
        : 3;

  const handleFile = useCallback((f: File) => {
    if (!f.name.toLowerCase().endsWith(".pdf")) {
      setError("Please select a PDF file.");
      return;
    }
    setFile(f);
    setError(null);
    setState("idle");
    setAnalysisResult(null);
    setSelectedProposalIds(new Set());
    setReport(null);
    // Clean up old download URL
    if (downloadUrl) {
      URL.revokeObjectURL(downloadUrl);
      setDownloadUrl(null);
    }
  }, [downloadUrl]);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const dropped = e.dataTransfer.files[0];
      if (dropped) handleFile(dropped);
    },
    [handleFile],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  // Step 1: Analyze
  const handleAnalyze = async () => {
    if (!file) return;

    setState("analyzing");
    setError(null);
    setAnalysisResult(null);

    try {
      const result = await analyzeDocument(file);
      setAnalysisResult(result);
      // Select all proposals by default
      setSelectedProposalIds(new Set(result.proposals.map((p) => p.id)));
      setState("proposals");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Analysis failed. Please try again.";
      setError(msg);
      setState("error");
    }
  };

  // Step 2: Toggle proposal selection
  const handleToggleProposal = useCallback((id: string) => {
    setSelectedProposalIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const handleToggleAll = useCallback(() => {
    if (!analysisResult) return;
    setSelectedProposalIds((prev) => {
      if (prev.size === analysisResult.proposals.length) {
        return new Set();
      }
      return new Set(analysisResult.proposals.map((p) => p.id));
    });
  }, [analysisResult]);

  // Step 3: Remediate
  const handleRemediate = async () => {
    if (!file) return;

    setState("remediating");
    setError(null);
    setReport(null);

    try {
      const { blob, taskId } = await remediateDocument(file, format);
      const url = URL.createObjectURL(blob);
      const stem = file.name.replace(/\.pdf$/i, "");
      setDownloadUrl(url);
      setDownloadName(`${stem}_remediated.${format}`);
      setState("done");

      // Fetch remediation report if task_id was returned
      if (taskId) {
        try {
          const remediationReport = await fetchRemediationReport(taskId);
          setReport(remediationReport);
        } catch {
          // Non-fatal — show download even if report fetch fails
          console.warn("Could not fetch remediation report");
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Remediation failed. Please try again.";
      setError(msg);
      setState("error");
    }
  };

  const handleReset = () => {
    setFile(null);
    setState("idle");
    setError(null);
    setAnalysisResult(null);
    setSelectedProposalIds(new Set());
    setReport(null);
    if (downloadUrl) {
      URL.revokeObjectURL(downloadUrl);
      setDownloadUrl(null);
    }
  };

  const handleBackToProposals = () => {
    setState("proposals");
    setError(null);
    setReport(null);
    if (downloadUrl) {
      URL.revokeObjectURL(downloadUrl);
      setDownloadUrl(null);
    }
  };

  return (
    <div className="container mx-auto max-w-2xl px-4 py-8 sm:px-6">
      <section aria-labelledby="upload-heading" className="rounded-xl border border-border bg-card p-6 shadow-sac sm:p-8">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-sac-navy text-white">
            <Upload className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h1 id="upload-heading" className="text-xl font-bold text-foreground sm:text-2xl">
              PDF Accessibility Remediation
            </h1>
            <p className="text-sm text-muted-foreground">
              Analyze, review, and remediate PDFs for WCAG 2.1 AA compliance
            </p>
          </div>
        </div>

        {/* Step indicator */}
        <StepIndicator currentStep={currentStep} />

        {/* ================================================================ */}
        {/* STEP 1: Upload & Analyze                                         */}
        {/* ================================================================ */}
        {(state === "idle" || state === "analyzing") && (
          <>
            {/* Drop zone */}
            <div
              role="button"
              tabIndex={0}
              aria-label={file ? `Selected file: ${file.name}. Click to change.` : "Click or drag a PDF file to upload"}
              className={`flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 transition-colors ${
                isDragging
                  ? "border-primary bg-primary/5"
                  : file
                    ? "border-green-500 bg-green-50"
                    : "border-border hover:border-primary/50 hover:bg-muted/50"
              }`}
              onClick={() => fileInputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,application/pdf"
                className="sr-only"
                aria-label="Choose PDF file"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleFile(f);
                }}
              />

              {file ? (
                <div className="flex items-center gap-3">
                  <FileText className="h-8 w-8 text-green-600" aria-hidden="true" />
                  <div>
                    <p className="font-medium text-foreground">{file.name}</p>
                    <p className="text-sm text-muted-foreground">
                      {(file.size / 1024).toFixed(1)} KB
                    </p>
                  </div>
                  <button
                    type="button"
                    aria-label="Remove selected file"
                    className="ml-2 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleReset();
                    }}
                  >
                    <X className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
              ) : (
                <>
                  <Upload className="h-10 w-10 text-muted-foreground" aria-hidden="true" />
                  <p className="mt-3 text-sm font-medium text-foreground">
                    Drop a PDF here or click to browse
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    PDF files only, up to 50 MB
                  </p>
                </>
              )}
            </div>

            {/* Analyze button */}
            <div className="mt-6">
              <button
                type="button"
                disabled={!file || state === "analyzing"}
                onClick={handleAnalyze}
                aria-label={state === "analyzing" ? "Analyzing document, please wait" : "Analyze document for accessibility issues"}
                className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-sac-navy px-6 py-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-sac-navy/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {state === "analyzing" ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    Analyzing document for accessibility issues...
                  </>
                ) : (
                  <>
                    <Search className="h-4 w-4" aria-hidden="true" />
                    Analyze Document
                  </>
                )}
              </button>
            </div>
          </>
        )}

        {/* ================================================================ */}
        {/* STEP 2: Review Proposals                                         */}
        {/* ================================================================ */}
        {state === "proposals" && analysisResult && (
          <>
            {/* Summary bar */}
            <AnalysisSummaryBar summary={analysisResult.summary} />

            {/* File info */}
            <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
              <FileText className="h-4 w-4" aria-hidden="true" />
              <span>
                {analysisResult.filename} — {analysisResult.page_count} page{analysisResult.page_count !== 1 ? "s" : ""}
              </span>
            </div>

            {/* Proposals list */}
            <div className="mt-4">
              <ProposalsList
                proposals={analysisResult.proposals}
                selectedIds={selectedProposalIds}
                onToggle={handleToggleProposal}
                onToggleAll={handleToggleAll}
              />
            </div>

            {/* Output format selector */}
            <fieldset className="mt-6">
              <legend className="text-sm font-medium text-foreground">
                Output Format
              </legend>
              <div className="mt-2 flex gap-4" role="radiogroup" aria-label="Output format">
                <label
                  className={`flex cursor-pointer items-center gap-2 rounded-md border px-4 py-2 text-sm transition-colors ${
                    format === "html"
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:border-primary/50"
                  }`}
                >
                  <input
                    type="radio"
                    name="format"
                    value="html"
                    checked={format === "html"}
                    onChange={() => setFormat("html")}
                    className="sr-only"
                  />
                  <span aria-hidden="true" className={`h-3 w-3 rounded-full border-2 ${
                    format === "html" ? "border-primary bg-primary" : "border-muted-foreground"
                  }`} />
                  HTML
                </label>
                <label
                  className={`flex cursor-pointer items-center gap-2 rounded-md border px-4 py-2 text-sm transition-colors ${
                    format === "pdf"
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:border-primary/50"
                  }`}
                >
                  <input
                    type="radio"
                    name="format"
                    value="pdf"
                    checked={format === "pdf"}
                    onChange={() => setFormat("pdf")}
                    className="sr-only"
                  />
                  <span aria-hidden="true" className={`h-3 w-3 rounded-full border-2 ${
                    format === "pdf" ? "border-primary bg-primary" : "border-muted-foreground"
                  }`} />
                  PDF/UA
                </label>
              </div>
            </fieldset>

            {/* Action buttons */}
            <div className="mt-6 flex gap-3">
              <button
                type="button"
                onClick={handleRemediate}
                disabled={selectedProposalIds.size === 0}
                aria-label={`Apply ${selectedProposalIds.size} selected remediation${selectedProposalIds.size !== 1 ? "s" : ""}`}
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Wrench className="h-4 w-4" aria-hidden="true" />
                Apply {selectedProposalIds.size} Remediation{selectedProposalIds.size !== 1 ? "s" : ""}
              </button>
              <button
                type="button"
                onClick={handleReset}
                className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-3 text-sm text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                aria-label="Start over with a different file"
              >
                <X className="h-4 w-4" aria-hidden="true" />
                Start Over
              </button>
            </div>
          </>
        )}

        {/* ================================================================ */}
        {/* STEP 3: Remediating (loading)                                    */}
        {/* ================================================================ */}
        {state === "remediating" && (
          <div className="flex flex-col items-center justify-center py-12" role="status" aria-live="polite">
            <Loader2 className="h-10 w-10 animate-spin text-sac-navy" aria-hidden="true" />
            <p className="mt-4 text-sm font-medium text-foreground">
              Applying remediations...
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              This may take 30-60 seconds depending on document size
            </p>
          </div>
        )}

        {/* ================================================================ */}
        {/* STEP 3: Done — Download                                          */}
        {/* ================================================================ */}
        {state === "done" && downloadUrl && (
          <div
            role="status"
            aria-live="polite"
            className="rounded-md border border-green-500/50 bg-green-50 p-4"
          >
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-green-100">
                <Download className="h-5 w-5 text-green-700" aria-hidden="true" />
              </div>
              <div className="flex-1">
                <p className="font-medium text-green-900">
                  Remediation complete
                </p>
                <p className="text-sm text-green-700">
                  Your WCAG 2.1 AA compliant document is ready to download.
                </p>
              </div>
            </div>
            <div className="mt-4 flex gap-3">
              <a
                href={downloadUrl}
                download={downloadName}
                className="inline-flex items-center gap-2 rounded-md bg-green-700 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-green-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <Download className="h-4 w-4" aria-hidden="true" />
                Download {downloadName}
              </a>
              <button
                type="button"
                onClick={handleReset}
                className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                Remediate Another
              </button>
            </div>
          </div>
        )}

        {/* Remediation report */}
        {state === "done" && report && (
          <RemediationSummary report={report} />
        )}

        {/* Post-remediation review */}
        {state === "done" && report && report.events.length > 0 && (
          <PostRemediationReview
            events={report.events}
            documentId={report.task_id}
          />
        )}

        {/* ================================================================ */}
        {/* Error state (can happen at any step)                              */}
        {/* ================================================================ */}
        {state === "error" && error && (
          <div className="space-y-4">
            <div
              role="alert"
              className="flex items-start gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <p>{error}</p>
            </div>
            <div className="flex gap-3">
              {analysisResult ? (
                <button
                  type="button"
                  onClick={handleBackToProposals}
                  className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                >
                  Back to Proposals
                </button>
              ) : null}
              <button
                type="button"
                onClick={handleReset}
                className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                Start Over
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
