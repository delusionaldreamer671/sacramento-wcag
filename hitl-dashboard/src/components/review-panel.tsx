"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import type { HITLReviewItem, ReviewerDecision, WCAGCriterion } from "@/lib/types";
import { WCAG_CRITERION_LABELS } from "@/lib/types";
import { fetchReviewItems } from "@/lib/api";
import { ElementViewer } from "./element-viewer";
import { ApprovalControls } from "./approval-controls";
import { ScreenReaderChecklist } from "./screen-reader-checklist";

interface ReviewPanelProps {
  documentId: string;
  /** Optional initial item ID to open. */
  initialItemId?: string;
  /** Reviewer identity forwarded to ApprovalControls. */
  reviewerId?: string;
  className?: string;
}

type LoadState = "idle" | "loading" | "error";

export function ReviewPanel({
  documentId,
  initialItemId,
  reviewerId = "reviewer",
  className,
}: ReviewPanelProps) {
  const [items, setItems] = useState<HITLReviewItem[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const approvalRef = useRef<HTMLDivElement>(null);
  const panelTopRef = useRef<HTMLDivElement>(null);

  // ----------------------------------------------------------------
  // Data loading
  // ----------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoadState("loading");
    setLoadError(null);

    fetchReviewItems(documentId)
      .then((data) => {
        if (cancelled) return;
        // Sort pending items first, then by element type
        const sorted = [...data].sort((a, b) => {
          if (a.reviewer_decision === null && b.reviewer_decision !== null) return -1;
          if (a.reviewer_decision !== null && b.reviewer_decision === null) return 1;
          return 0;
        });
        setItems(sorted);

        if (initialItemId) {
          const idx = sorted.findIndex((i) => i.id === initialItemId);
          setCurrentIndex(idx >= 0 ? idx : 0);
        } else {
          setCurrentIndex(0);
        }
        setLoadState("idle");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(
          err instanceof Error ? err.message : "Failed to load review items.",
        );
        setLoadState("error");
      });

    return () => {
      cancelled = true;
    };
  }, [documentId, initialItemId]);

  // ----------------------------------------------------------------
  // Navigation
  // ----------------------------------------------------------------
  const currentItem = items[currentIndex] ?? null;

  const canGoPrev = currentIndex > 0;
  const canGoNext = currentIndex < items.length - 1;

  const goToPrev = useCallback(() => {
    if (canGoPrev) {
      setCurrentIndex((i) => i - 1);
      panelTopRef.current?.focus();
    }
  }, [canGoPrev]);

  const goToNext = useCallback(() => {
    if (canGoNext) {
      setCurrentIndex((i) => i + 1);
      panelTopRef.current?.focus();
    }
  }, [canGoNext]);

  // Arrow-key navigation within the panel
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (isSubmitting) return;
      // Only navigate with bare Left/Right — Alt+key is reserved for approval shortcuts
      if (event.altKey || event.ctrlKey || event.metaKey) return;

      const tag = (event.target as HTMLElement).tagName.toLowerCase();
      if (tag === "textarea" || tag === "input") return;

      if (event.key === "ArrowLeft") {
        event.preventDefault();
        goToPrev();
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        goToNext();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [goToPrev, goToNext, isSubmitting]);

  // ----------------------------------------------------------------
  // Decision callback
  // ----------------------------------------------------------------
  function handleDecisionSubmitted(
    itemId: string,
    decision: ReviewerDecision,
    editContent?: string,
  ) {
    setItems((prev) =>
      prev.map((item) =>
        item.id === itemId
          ? {
              ...item,
              reviewer_decision: decision,
              reviewer_edit: editContent ?? item.reviewer_edit,
              reviewed_at: new Date().toISOString(),
              reviewed_by: reviewerId,
            }
          : item,
      ),
    );
    // Auto-advance to next pending item after a short delay
    setTimeout(() => {
      const nextPendingIndex = items.findIndex(
        (item, idx) => idx > currentIndex && item.reviewer_decision === null,
      );
      if (nextPendingIndex >= 0) {
        setCurrentIndex(nextPendingIndex);
        panelTopRef.current?.focus();
      }
    }, 800);
  }

  // ----------------------------------------------------------------
  // Derived stats
  // ----------------------------------------------------------------
  const pendingCount = items.filter((i) => i.reviewer_decision === null).length;
  const reviewedCount = items.length - pendingCount;

  // ----------------------------------------------------------------
  // Render
  // ----------------------------------------------------------------
  if (loadState === "loading") {
    return (
      <div
        role="status"
        aria-label="Loading review items"
        className={cn(
          "flex min-h-[300px] items-center justify-center rounded-lg border border-border",
          className,
        )}
      >
        <div className="flex flex-col items-center gap-3 text-muted-foreground">
          <svg
            className="h-8 w-8 animate-spin"
            aria-hidden="true"
            fill="none"
            viewBox="0 0 24 24"
          >
            <path
              className="opacity-25"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          <p className="text-sm">Loading review items…</p>
        </div>
      </div>
    );
  }

  if (loadState === "error") {
    return (
      <div
        role="alert"
        className={cn(
          "rounded-lg border border-destructive bg-destructive/10 p-6 text-center",
          className,
        )}
      >
        <p className="text-sm font-medium text-destructive">
          Failed to load review items
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{loadError}</p>
        <button
          onClick={() => {
            setLoadState("loading");
            fetchReviewItems(documentId)
              .then((data) => {
                setItems(data);
                setLoadState("idle");
              })
              .catch(() => setLoadState("error"));
          }}
          className={cn(
            "mt-3 rounded-md bg-destructive px-3 py-1.5 text-xs font-medium text-destructive-foreground",
            "hover:bg-destructive/90 focus-visible:ring-2 focus-visible:ring-destructive focus-visible:ring-offset-2",
          )}
        >
          Retry
        </button>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div
        className={cn(
          "flex min-h-[200px] items-center justify-center rounded-lg border border-border bg-muted/30 p-6 text-center",
          className,
        )}
      >
        <div>
          <p className="font-medium text-foreground">No review items</p>
          <p className="mt-1 text-sm text-muted-foreground">
            This document has no items requiring HITL review.
          </p>
        </div>
      </div>
    );
  }

  if (!currentItem) return null;

  const wcagLabel =
    WCAG_CRITERION_LABELS[currentItem.finding_id as WCAGCriterion] ??
    currentItem.element_type;

  return (
    <div className={cn("space-y-4", className)}>
      {/* Skip link to jump to approval controls */}
      <a href="#approval-controls" className="skip-link">
        Skip to approval controls
      </a>

      {/* Panel header — progress + navigation */}
      <div
        ref={panelTopRef}
        tabIndex={-1}
        className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4 sm:flex-row sm:items-center sm:justify-between"
        aria-label={`Review item ${currentIndex + 1} of ${items.length}`}
      >
        <div>
          <h2 className="text-base font-semibold text-foreground">
            Review Item{" "}
            <span aria-current="step">
              {currentIndex + 1}
            </span>{" "}
            of{" "}
            <span>{items.length}</span>
          </h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {pendingCount} pending · {reviewedCount} reviewed
          </p>
        </div>

        {/* Progress bar */}
        <div className="flex-1 sm:max-w-[200px]">
          <div
            role="progressbar"
            aria-valuenow={reviewedCount}
            aria-valuemin={0}
            aria-valuemax={items.length}
            aria-label={`${reviewedCount} of ${items.length} items reviewed`}
            className="h-2 overflow-hidden rounded-full bg-muted"
          >
            <div
              className="h-full bg-primary transition-all duration-300"
              style={{
                width: `${(reviewedCount / items.length) * 100}%`,
              }}
            />
          </div>
        </div>

        {/* Prev/Next navigation */}
        <nav aria-label="Review item navigation" className="flex items-center gap-1">
          <button
            type="button"
            onClick={goToPrev}
            disabled={!canGoPrev || isSubmitting}
            aria-label="Previous review item (Left arrow key)"
            className={cn(
              "rounded-md p-2 text-sm text-muted-foreground",
              "hover:bg-muted hover:text-foreground",
              "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              "disabled:cursor-not-allowed disabled:opacity-40 transition-colors",
            )}
          >
            <ChevronLeftIcon aria-hidden="true" />
          </button>
          <span className="px-1 text-xs text-muted-foreground">
            {currentIndex + 1} / {items.length}
          </span>
          <button
            type="button"
            onClick={goToNext}
            disabled={!canGoNext || isSubmitting}
            aria-label="Next review item (Right arrow key)"
            className={cn(
              "rounded-md p-2 text-sm text-muted-foreground",
              "hover:bg-muted hover:text-foreground",
              "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              "disabled:cursor-not-allowed disabled:opacity-40 transition-colors",
            )}
          >
            <ChevronRightIcon aria-hidden="true" />
          </button>
        </nav>
      </div>

      {/* Item metadata strip */}
      <div className="flex flex-wrap gap-2 px-1">
        <ElementTypeBadge type={currentItem.element_type} />
        <span className="inline-flex items-center rounded-full border border-border bg-muted px-2.5 py-0.5 text-xs text-muted-foreground">
          WCAG {currentItem.finding_id} — {wcagLabel}
        </span>
        {currentItem.reviewer_decision !== null && (
          <DecisionBadge decision={currentItem.reviewer_decision} />
        )}
      </div>

      {/* Side-by-side comparison */}
      <div
        className="review-grid"
        aria-label="Original element and AI suggestion comparison"
      >
        {/* Left: Original element */}
        <section
          aria-labelledby="original-heading"
          className="rounded-lg border border-border bg-card p-4"
        >
          <h3
            id="original-heading"
            className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground"
          >
            Original Element
          </h3>
          <ElementViewer item={currentItem} />
        </section>

        {/* Right: AI suggestion */}
        <section
          aria-labelledby="suggestion-heading"
          className="rounded-lg border border-border bg-card p-4"
        >
          <h3
            id="suggestion-heading"
            className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground"
          >
            AI-Generated Suggestion
          </h3>
          <AISuggestionViewer
            suggestion={currentItem.ai_suggestion}
            elementType={currentItem.element_type}
            reviewerEdit={currentItem.reviewer_edit}
          />
        </section>
      </div>

      {/* Approval controls */}
      <div
        id="approval-controls"
        ref={approvalRef}
        className="rounded-lg border border-border bg-card p-4"
        aria-label="Approval decision section"
      >
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Your Decision
        </h3>
        <ApprovalControls
          item={currentItem}
          onDecisionSubmitted={handleDecisionSubmitted}
          onSubmittingChange={setIsSubmitting}
          reviewerId={reviewerId}
          disabled={isSubmitting}
        />
      </div>

      {/* Screen reader testing checklist */}
      <ScreenReaderChecklist />

      <p className="text-xs text-muted-foreground">
        Use <kbd className="kbd">←</kbd> / <kbd className="kbd">→</kbd> arrow
        keys to navigate between items.
      </p>
    </div>
  );
}

/** ------------------------------------------------------------------ *
 *  AI Suggestion display sub-component                                 *
 * ------------------------------------------------------------------- */

function AISuggestionViewer({
  suggestion,
  elementType,
  reviewerEdit,
}: {
  suggestion: string;
  elementType: string;
  reviewerEdit: string | null;
}) {
  const displayValue = reviewerEdit ?? suggestion;
  const isEdited = Boolean(reviewerEdit);

  if (elementType === "table") {
    return (
      <div className="space-y-2">
        {isEdited && (
          <p className="text-xs text-amber-600 font-medium">Reviewer-edited version shown</p>
        )}
        <div
          className="rounded-md border border-border bg-muted/40 overflow-x-auto"
          aria-label="AI-suggested semantic HTML table structure"
        >
          <pre className="p-3 text-xs whitespace-pre-wrap break-words font-mono text-foreground">
            {displayValue}
          </pre>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {isEdited && (
        <p className="text-xs text-amber-600 font-medium">Reviewer-edited version shown</p>
      )}
      <blockquote
        className="rounded-md border-l-4 border-primary bg-primary/5 p-3 text-sm text-foreground"
        aria-label={`AI-suggested ${elementType} remediation`}
      >
        {displayValue}
      </blockquote>
      <p className="text-xs text-muted-foreground">
        AI-generated suggestion for {elementType} element
      </p>
    </div>
  );
}

/** ------------------------------------------------------------------ *
 *  Small badge components                                              *
 * ------------------------------------------------------------------- */

function ElementTypeBadge({ type }: { type: string }) {
  const colorMap: Record<string, string> = {
    image: "bg-blue-100 text-blue-800 border-blue-200",
    figure: "bg-blue-100 text-blue-800 border-blue-200",
    table: "bg-purple-100 text-purple-800 border-purple-200",
    heading: "bg-green-100 text-green-800 border-green-200",
    link: "bg-orange-100 text-orange-800 border-orange-200",
  };
  const color = colorMap[type] ?? "bg-muted text-muted-foreground border-border";

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold capitalize",
        color,
      )}
    >
      {type}
    </span>
  );
}

function DecisionBadge({ decision }: { decision: ReviewerDecision }) {
  const map: Record<ReviewerDecision, { label: string; color: string }> = {
    approve: { label: "Approved", color: "bg-green-100 text-green-800 border-green-200" },
    edit: { label: "Edited", color: "bg-amber-100 text-amber-800 border-amber-200" },
    reject: { label: "Rejected", color: "bg-red-100 text-red-800 border-red-200" },
  };
  const { label, color } = map[decision];

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold",
        color,
      )}
    >
      {label}
    </span>
  );
}

/** ------------------------------------------------------------------ *
 *  Icons                                                               *
 * ------------------------------------------------------------------- */

function ChevronLeftIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={18}
      height={18}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}

function ChevronRightIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={18}
      height={18}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}
