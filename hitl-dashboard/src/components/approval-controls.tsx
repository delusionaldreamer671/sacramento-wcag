"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import type { HITLReviewItem, ReviewDecisionPayload, ReviewerDecision } from "@/lib/types";

interface ApprovalControlsProps {
  item: HITLReviewItem;
  /** Called after a decision is submitted successfully. */
  onDecisionSubmitted: (
    itemId: string,
    decision: ReviewerDecision,
    editContent?: string,
  ) => void;
  /** Called when submission is in progress so the parent can disable navigation. */
  onSubmittingChange?: (submitting: boolean) => void;
  /** Reviewer identity (e.g. authenticated user email). Defaults to "reviewer". */
  reviewerId?: string;
  /** Whether controls should be disabled (e.g. during parent navigation). */
  disabled?: boolean;
  className?: string;
}

type ActivePanel = "none" | "edit" | "reject";

export function ApprovalControls({
  item,
  onDecisionSubmitted,
  onSubmittingChange,
  reviewerId = "reviewer",
  disabled = false,
  className,
}: ApprovalControlsProps) {
  const [activePanel, setActivePanel] = useState<ActivePanel>("none");
  const [editValue, setEditValue] = useState(item.ai_suggestion);
  const [rejectReason, setRejectReason] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const editTextareaRef = useRef<HTMLTextAreaElement>(null);
  const rejectTextareaRef = useRef<HTMLTextAreaElement>(null);
  const approveButtonRef = useRef<HTMLButtonElement>(null);

  // Reset local state when the item changes
  useEffect(() => {
    setActivePanel("none");
    setEditValue(item.ai_suggestion);
    setRejectReason("");
    setError(null);
    setSuccessMessage(null);
  }, [item.id, item.ai_suggestion]);

  // Focus textarea when panel opens
  useEffect(() => {
    if (activePanel === "edit") {
      editTextareaRef.current?.focus();
    } else if (activePanel === "reject") {
      rejectTextareaRef.current?.focus();
    }
  }, [activePanel]);

  // Keyboard shortcuts: Alt+A, Alt+E, Alt+R
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (!event.altKey || disabled || isSubmitting) return;

      switch (event.key.toLowerCase()) {
        case "a":
          event.preventDefault();
          handleApprove();
          break;
        case "e":
          event.preventDefault();
          setActivePanel((prev) => (prev === "edit" ? "none" : "edit"));
          break;
        case "r":
          event.preventDefault();
          setActivePanel((prev) => (prev === "reject" ? "none" : "reject"));
          break;
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disabled, isSubmitting, item]);

  const setSubmitting = useCallback(
    (value: boolean) => {
      setIsSubmitting(value);
      onSubmittingChange?.(value);
    },
    [onSubmittingChange],
  );

  async function submitDecision(payload: ReviewDecisionPayload) {
    setError(null);
    setSubmitting(true);
    try {
      // Dynamic import to avoid circular reference between api.ts and this component
      const { submitReview } = await import("@/lib/api");
      await submitReview(item.id, payload);
      setSuccessMessage(
        payload.decision === "approve"
          ? "Approved successfully."
          : payload.decision === "edit"
            ? "Edit submitted successfully."
            : "Rejected — flagged for manual remediation.",
      );
      onDecisionSubmitted(item.id, payload.decision, payload.reviewer_edit);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Submission failed. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  function handleApprove() {
    if (isSubmitting || disabled) return;
    setActivePanel("none");
    void submitDecision({ decision: "approve", reviewed_by: reviewerId });
  }

  function handleEditSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = editValue.trim();
    if (!trimmed) {
      setError("Edited content cannot be empty.");
      editTextareaRef.current?.focus();
      return;
    }
    void submitDecision({
      decision: "edit",
      reviewer_edit: trimmed,
      reviewed_by: reviewerId,
    });
  }

  function handleRejectSubmit(event: React.FormEvent) {
    event.preventDefault();
    void submitDecision({
      decision: "reject",
      reviewer_edit: rejectReason.trim() || undefined,
      reviewed_by: reviewerId,
    });
  }

  // If already reviewed, show read-only badge
  if (item.reviewer_decision !== null) {
    const decisionLabels: Record<ReviewerDecision, string> = {
      approve: "Approved",
      edit: "Edited and approved",
      reject: "Rejected — manual remediation required",
    };
    const decisionColors: Record<ReviewerDecision, string> = {
      approve: "bg-green-100 text-green-800 border-green-200",
      edit: "bg-amber-100 text-amber-800 border-amber-200",
      reject: "bg-red-100 text-red-800 border-red-200",
    };

    return (
      <div
        className={cn("rounded-md border p-3", decisionColors[item.reviewer_decision], className)}
        role="status"
        aria-live="polite"
      >
        <p className="text-sm font-medium">
          {decisionLabels[item.reviewer_decision]}
        </p>
        {item.reviewed_at && (
          <p className="mt-0.5 text-xs opacity-75">
            {new Date(item.reviewed_at).toLocaleString()}
            {item.reviewed_by ? ` by ${item.reviewed_by}` : ""}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className={cn("space-y-3", className)} aria-label="Review decision controls">
      {/* Success message */}
      {successMessage && (
        <div
          role="status"
          aria-live="polite"
          className="rounded-md bg-green-50 border border-green-200 px-3 py-2 text-sm text-green-800"
        >
          {successMessage}
        </div>
      )}

      {/* Error message */}
      {error && (
        <div
          role="alert"
          aria-live="assertive"
          className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      {/* Keyboard shortcut hint */}
      <p className="text-xs text-muted-foreground" id="keyboard-shortcuts-hint">
        Keyboard shortcuts:{" "}
        <kbd className="kbd" aria-label="Alt+A to approve">Alt+A</kbd> Approve{" "}
        <kbd className="kbd" aria-label="Alt+E to edit">Alt+E</kbd> Edit{" "}
        <kbd className="kbd" aria-label="Alt+R to reject">Alt+R</kbd> Reject
      </p>

      {/* Primary action buttons */}
      <div
        role="group"
        aria-label="Decision buttons"
        aria-describedby="keyboard-shortcuts-hint"
        className="flex flex-wrap gap-2"
      >
        {/* Approve */}
        <button
          ref={approveButtonRef}
          type="button"
          onClick={handleApprove}
          disabled={isSubmitting || disabled}
          aria-label="Approve AI suggestion (Alt+A)"
          className={cn(
            "inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium",
            "bg-green-600 text-white",
            "hover:bg-green-700 focus-visible:ring-2 focus-visible:ring-green-600 focus-visible:ring-offset-2",
            "disabled:cursor-not-allowed disabled:opacity-50",
            "transition-colors",
          )}
        >
          {isSubmitting && activePanel === "none" ? (
            <Spinner aria-hidden="true" />
          ) : (
            <CheckIcon aria-hidden="true" />
          )}
          Approve
        </button>

        {/* Edit toggle */}
        <button
          type="button"
          onClick={() =>
            setActivePanel((prev) => (prev === "edit" ? "none" : "edit"))
          }
          disabled={isSubmitting || disabled}
          aria-label="Edit AI suggestion before approving (Alt+E)"
          aria-expanded={activePanel === "edit"}
          aria-controls="edit-panel"
          className={cn(
            "inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium",
            "border border-amber-400 bg-amber-50 text-amber-800",
            "hover:bg-amber-100 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2",
            "disabled:cursor-not-allowed disabled:opacity-50",
            "transition-colors",
            activePanel === "edit" && "bg-amber-100 ring-2 ring-amber-500",
          )}
        >
          <PencilIcon aria-hidden="true" />
          Edit
        </button>

        {/* Reject toggle */}
        <button
          type="button"
          onClick={() =>
            setActivePanel((prev) => (prev === "reject" ? "none" : "reject"))
          }
          disabled={isSubmitting || disabled}
          aria-label="Reject AI suggestion and flag for manual remediation (Alt+R)"
          aria-expanded={activePanel === "reject"}
          aria-controls="reject-panel"
          className={cn(
            "inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium",
            "border border-red-300 bg-red-50 text-red-700",
            "hover:bg-red-100 focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2",
            "disabled:cursor-not-allowed disabled:opacity-50",
            "transition-colors",
            activePanel === "reject" && "bg-red-100 ring-2 ring-red-500",
          )}
        >
          <XIcon aria-hidden="true" />
          Reject
        </button>
      </div>

      {/* Edit panel */}
      {activePanel === "edit" && (
        <form
          id="edit-panel"
          onSubmit={handleEditSubmit}
          className="rounded-md border border-amber-200 bg-amber-50/50 p-3 space-y-3"
          aria-label="Edit AI suggestion form"
        >
          <label
            htmlFor="edit-textarea"
            className="block text-sm font-medium text-amber-900"
          >
            Edit the AI-generated suggestion:
          </label>
          <textarea
            id="edit-textarea"
            ref={editTextareaRef}
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            rows={5}
            required
            aria-required="true"
            aria-describedby="edit-help"
            className={cn(
              "w-full rounded-md border border-amber-300 bg-white px-3 py-2 text-sm",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500",
              "resize-y",
            )}
          />
          <p id="edit-help" className="text-xs text-amber-700">
            Modify the alt text or tag structure, then submit for approval.
          </p>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={isSubmitting}
              aria-label="Submit edited suggestion"
              className={cn(
                "inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium",
                "bg-amber-600 text-white hover:bg-amber-700",
                "focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:ring-offset-2",
                "disabled:cursor-not-allowed disabled:opacity-50 transition-colors",
              )}
            >
              {isSubmitting && <Spinner aria-hidden="true" />}
              Submit Edit
            </button>
            <button
              type="button"
              onClick={() => setActivePanel("none")}
              disabled={isSubmitting}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium text-amber-800",
                "hover:bg-amber-100 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2",
                "disabled:cursor-not-allowed disabled:opacity-50 transition-colors",
              )}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {/* Reject panel */}
      {activePanel === "reject" && (
        <form
          id="reject-panel"
          onSubmit={handleRejectSubmit}
          className="rounded-md border border-red-200 bg-red-50/50 p-3 space-y-3"
          aria-label="Reject and flag for manual remediation form"
        >
          <label
            htmlFor="reject-textarea"
            className="block text-sm font-medium text-red-900"
          >
            Rejection reason{" "}
            <span className="font-normal text-red-700">(optional)</span>:
          </label>
          <textarea
            id="reject-textarea"
            ref={rejectTextareaRef}
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            rows={3}
            aria-describedby="reject-help"
            placeholder="Describe why the AI suggestion is insufficient…"
            className={cn(
              "w-full rounded-md border border-red-300 bg-white px-3 py-2 text-sm",
              "placeholder:text-muted-foreground",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500",
              "resize-y",
            )}
          />
          <p id="reject-help" className="text-xs text-red-700">
            This item will be flagged for manual remediation by an accessibility
            specialist.
          </p>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={isSubmitting}
              aria-label="Confirm rejection and flag for manual remediation"
              className={cn(
                "inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium",
                "bg-red-600 text-white hover:bg-red-700",
                "focus-visible:ring-2 focus-visible:ring-red-600 focus-visible:ring-offset-2",
                "disabled:cursor-not-allowed disabled:opacity-50 transition-colors",
              )}
            >
              {isSubmitting && <Spinner aria-hidden="true" />}
              Confirm Reject
            </button>
            <button
              type="button"
              onClick={() => setActivePanel("none")}
              disabled={isSubmitting}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium text-red-800",
                "hover:bg-red-100 focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2",
                "disabled:cursor-not-allowed disabled:opacity-50 transition-colors",
              )}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

/** ------------------------------------------------------------------ *
 *  Inline icon components — avoids extra import overhead               *
 * ------------------------------------------------------------------- */

function CheckIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={16}
      height={16}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function PencilIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={16}
      height={16}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

function XIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={16}
      height={16}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function Spinner(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={16}
      height={16}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="animate-spin"
      {...props}
    >
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}
