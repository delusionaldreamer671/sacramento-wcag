"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  fetchAltTextProposals,
  submitAltTextDecision,
  batchApproveAltText,
} from "@/lib/api";
import type { AltTextProposal } from "@/lib/api";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AltTextReviewPage() {
  const params = useParams();
  const router = useRouter();
  const taskId = params.taskId as string;

  const [proposals, setProposals] = useState<AltTextProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentIdx, setCurrentIdx] = useState(0);
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        setLoading(true);
        const data = await fetchAltTextProposals(taskId);
        if (cancelled) return;
        setProposals(data.proposals);
        // Initialize edit values with proposed alt text
        const edits: Record<string, string> = {};
        data.proposals.forEach((p: AltTextProposal) => {
          edits[p.id] = p.proposed_alt;
        });
        setEditValues(edits);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load proposals");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [taskId]);

  const current = proposals[currentIdx] ?? null;
  const pendingCount = proposals.filter((p) => p.status === "pending").length;
  const reviewedCount = proposals.length - pendingCount;

  const handleDecision = useCallback(async (
    proposalId: string,
    decision: string,
    editText?: string,
  ) => {
    setSubmitting(true);
    setActionFeedback(null);
    try {
      const updated = await submitAltTextDecision(
        proposalId,
        decision,
        editText,
        "reviewer",
      );
      setProposals((prev) =>
        prev.map((p) => (p.id === proposalId ? { ...p, ...updated } : p)),
      );
      setActionFeedback(
        decision === "approve"
          ? "Approved"
          : decision === "edit"
            ? "Edit saved"
            : "Rejected",
      );
      // Auto-advance to next pending after a short delay
      setTimeout(() => {
        setActionFeedback(null);
        const nextPending = proposals.findIndex(
          (p, i) => i > currentIdx && p.status === "pending" && p.id !== proposalId,
        );
        if (nextPending >= 0) {
          setCurrentIdx(nextPending);
        }
      }, 500);
    } catch (err) {
      setActionFeedback(
        err instanceof Error ? err.message : "Action failed",
      );
    } finally {
      setSubmitting(false);
    }
  }, [proposals, currentIdx]);

  const handleBatchApprove = useCallback(async () => {
    const pendingIds = proposals
      .filter((p) => p.status === "pending")
      .map((p) => p.id);
    if (pendingIds.length === 0) return;

    setSubmitting(true);
    try {
      await batchApproveAltText(pendingIds, "reviewer");
      setProposals((prev) =>
        prev.map((p) =>
          pendingIds.includes(p.id)
            ? { ...p, status: "approved", reviewer_decision: "approve" }
            : p,
        ),
      );
      setActionFeedback(`Approved ${pendingIds.length} proposals`);
    } catch (err) {
      setActionFeedback(
        err instanceof Error ? err.message : "Batch approve failed",
      );
    } finally {
      setSubmitting(false);
    }
  }, [proposals]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-blue-600" />
          <p className="text-sm text-muted-foreground">Loading alt text proposals...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-center">
          <p className="font-medium text-red-800">Error</p>
          <p className="mt-1 text-sm text-red-600">{error}</p>
          <button
            type="button"
            onClick={() => router.push("/upload")}
            className="mt-4 rounded-md bg-red-600 px-3 py-1.5 text-sm text-white hover:bg-red-700"
          >
            Back to Upload
          </button>
        </div>
      </div>
    );
  }

  if (proposals.length === 0) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="rounded-lg border bg-white p-8 text-center shadow-sm">
          <p className="text-lg font-medium">No images found</p>
          <p className="mt-1 text-sm text-muted-foreground">
            This document has no images requiring alt text review.
          </p>
          <button
            type="button"
            onClick={() => router.push("/upload")}
            className="mt-4 rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
          >
            Back to Upload
          </button>
        </div>
      </div>
    );
  }

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Header */}
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Alt Text Review</h1>
          <p className="text-sm text-muted-foreground">
            Task: <span className="font-mono">{taskId.slice(0, 8)}...</span>
            {" | "}
            {reviewedCount}/{proposals.length} reviewed
            {" | "}
            {pendingCount} pending
          </p>
        </div>
        <div className="flex gap-2">
          {pendingCount > 0 && (
            <button
              type="button"
              onClick={handleBatchApprove}
              disabled={submitting}
              className="rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
            >
              Approve All Pending ({pendingCount})
            </button>
          )}
          <button
            type="button"
            onClick={() => router.push("/upload")}
            className="rounded-md border px-3 py-1.5 text-sm hover:bg-gray-50"
          >
            Back to Upload
          </button>
        </div>
      </header>

      {actionFeedback && (
        <div className="mb-4 rounded-md bg-blue-50 border border-blue-200 px-3 py-2 text-sm text-blue-800" role="status">
          {actionFeedback}
        </div>
      )}

      {/* Navigation strip */}
      <div className="mb-4 flex flex-wrap gap-1" role="navigation" aria-label="Image proposals">
        {proposals.map((p, i) => (
          <button
            key={p.id}
            type="button"
            onClick={() => setCurrentIdx(i)}
            aria-label={`Image ${i + 1}, page ${p.page_num + 1}, ${p.status}`}
            aria-current={i === currentIdx ? "true" : undefined}
            className={cn(
              "h-8 w-8 rounded text-xs font-medium transition-colors",
              i === currentIdx
                ? "bg-blue-600 text-white ring-2 ring-blue-400 ring-offset-1"
                : p.status === "approved"
                  ? "bg-green-100 text-green-800 hover:bg-green-200"
                  : p.status === "rejected"
                    ? "bg-red-100 text-red-800 hover:bg-red-200"
                    : "bg-gray-100 text-gray-700 hover:bg-gray-200",
            )}
          >
            {i + 1}
          </button>
        ))}
      </div>

      {/* Current proposal detail */}
      {current && (
        <ProposalDetail
          proposal={current}
          editValue={editValues[current.id] ?? current.proposed_alt}
          onEditChange={(val) =>
            setEditValues((prev) => ({ ...prev, [current.id]: val }))
          }
          onApprove={() => handleDecision(current.id, "approve")}
          onEdit={() =>
            handleDecision(current.id, "edit", editValues[current.id])
          }
          onReject={() => handleDecision(current.id, "reject")}
          submitting={submitting}
          imageUrl={
            current.image_id
              ? `${process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000"}/api/images/${current.image_id}`
              : undefined
          }
        />
      )}

      {/* Prev / Next navigation */}
      <div className="mt-4 flex justify-between">
        <button
          type="button"
          onClick={() => setCurrentIdx(Math.max(0, currentIdx - 1))}
          disabled={currentIdx === 0}
          className="rounded-md border px-4 py-2 text-sm hover:bg-gray-50 disabled:opacity-50"
        >
          Previous
        </button>
        <button
          type="button"
          onClick={() => setCurrentIdx(Math.min(proposals.length - 1, currentIdx + 1))}
          disabled={currentIdx === proposals.length - 1}
          className="rounded-md border px-4 py-2 text-sm hover:bg-gray-50 disabled:opacity-50"
        >
          Next
        </button>
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// ProposalDetail
// ---------------------------------------------------------------------------

function ProposalDetail({
  proposal,
  editValue,
  onEditChange,
  onApprove,
  onEdit,
  onReject,
  submitting,
  imageUrl,
}: {
  proposal: AltTextProposal;
  editValue: string;
  onEditChange: (val: string) => void;
  onApprove: () => void;
  onEdit: () => void;
  onReject: () => void;
  submitting: boolean;
  imageUrl?: string;
}) {
  const isReviewed = proposal.status !== "pending";

  return (
    <div className="grid gap-6 rounded-lg border bg-white p-6 md:grid-cols-2">
      {/* Left: Image preview */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-gray-600">
          Image (Page {proposal.page_num + 1})
        </h2>
        <div className="flex aspect-video items-center justify-center rounded-lg border bg-gray-50">
          {imageUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={imageUrl}
              alt={proposal.proposed_alt || "Image preview"}
              className="max-h-full max-w-full object-contain"
              loading="lazy"
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              No image preview available
            </p>
          )}
        </div>
        <div className="mt-2 flex gap-2 text-xs text-muted-foreground">
          <span className={cn(
            "rounded border px-1.5 py-0.5",
            proposal.image_classification === "complex"
              ? "border-amber-200 bg-amber-50 text-amber-700"
              : "border-gray-200 bg-gray-50",
          )}>
            {proposal.image_classification}
          </span>
          {proposal.confidence > 0 && (
            <span className="rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5">
              Confidence: {Math.round(proposal.confidence * 100)}%
            </span>
          )}
          <span className={cn(
            "rounded border px-1.5 py-0.5",
            proposal.status === "approved"
              ? "border-green-200 bg-green-50 text-green-700"
              : proposal.status === "rejected"
                ? "border-red-200 bg-red-50 text-red-700"
                : "border-gray-200 bg-gray-50",
          )}>
            {proposal.status}
          </span>
        </div>
      </div>

      {/* Right: Alt text + actions */}
      <div className="space-y-4">
        {proposal.original_alt && (
          <div>
            <h3 className="mb-1 text-xs font-medium text-gray-500">Original Alt Text</h3>
            <p className="rounded border bg-gray-50 px-3 py-2 text-sm text-gray-700">
              {proposal.original_alt || <em className="text-muted-foreground">None</em>}
            </p>
          </div>
        )}

        <div>
          <h3 className="mb-1 text-xs font-medium text-gray-500">
            {isReviewed ? "Final Alt Text" : "AI-Proposed Alt Text"}
          </h3>
          {isReviewed ? (
            <p className="rounded border bg-green-50 px-3 py-2 text-sm text-gray-700">
              {(proposal.reviewer_edit ?? proposal.proposed_alt) || <em className="text-muted-foreground">None</em>}
            </p>
          ) : (
            <textarea
              value={editValue}
              onChange={(e) => onEditChange(e.target.value)}
              rows={4}
              aria-label="Alt text editor"
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-y"
            />
          )}
        </div>

        {!isReviewed && (
          <div className="flex gap-2" role="group" aria-label="Review actions">
            <button
              type="button"
              onClick={onApprove}
              disabled={submitting}
              className="rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
            >
              Approve
            </button>
            <button
              type="button"
              onClick={onEdit}
              disabled={submitting || !editValue.trim()}
              className="rounded-md border border-amber-400 bg-amber-50 px-4 py-2 text-sm font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50"
            >
              Save Edit
            </button>
            <button
              type="button"
              onClick={onReject}
              disabled={submitting}
              className="rounded-md border border-red-300 bg-red-50 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
            >
              Reject
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
