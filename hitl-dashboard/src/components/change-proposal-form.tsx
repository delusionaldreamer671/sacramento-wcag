"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { createProposal } from "@/lib/api";
import type { ChangeProposal, SystemEvaluation } from "@/lib/types";

interface ChangeProposalFormProps {
  documentId: string;
  reviewItemId?: string;
  elementType?: string;
  findingSeverity?: string;
  findingCriterion?: string;
  className?: string;
}

export function ChangeProposalForm({
  documentId,
  reviewItemId,
  elementType = "paragraph",
  findingSeverity,
  findingCriterion,
  className,
}: ChangeProposalFormProps) {
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<ChangeProposal | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!comment.trim()) return;

    setSubmitting(true);
    setError(null);

    try {
      const proposal = await createProposal({
        document_id: documentId,
        review_item_id: reviewItemId,
        human_comment: comment.trim(),
        element_type: elementType,
        finding_severity: findingSeverity,
        finding_criterion: findingCriterion,
      });
      setResult(proposal);
      setComment("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit proposal");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={cn("space-y-3", className)}>
      <h4 className="text-sm font-semibold text-foreground">Propose a Change</h4>

      <form onSubmit={handleSubmit} className="space-y-2">
        <label className="block">
          <span className="text-xs text-muted-foreground">Describe your proposed change</span>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="e.g., Change alt text to describe the chart data..."
            rows={3}
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            required
          />
        </label>
        <button
          type="submit"
          disabled={submitting || !comment.trim()}
          className={cn(
            "rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground",
            "hover:bg-primary/90 disabled:opacity-50",
          )}
        >
          {submitting ? "Evaluating..." : "Submit Proposal"}
        </button>
      </form>

      {error && (
        <div role="alert" className="rounded-md border border-destructive bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {result && (
        <EvaluationDisplay proposal={result} />
      )}
    </div>
  );
}

function EvaluationDisplay({ proposal }: { proposal: ChangeProposal }) {
  const eval_ = proposal.system_evaluation as SystemEvaluation;

  const impactColor = {
    positive: "text-green-700 bg-green-50",
    neutral: "text-slate-700 bg-slate-50",
    negative: "text-red-700 bg-red-50",
  }[eval_.compliance_impact] ?? "text-slate-700 bg-slate-50";

  const riskColor = {
    low: "text-green-700",
    medium: "text-amber-700",
    high: "text-red-700",
  }[eval_.risk] ?? "text-slate-700";

  return (
    <div className="rounded-lg border border-border bg-muted/30 p-3 space-y-2">
      <div className="flex items-center justify-between">
        <h5 className="text-xs font-semibold text-foreground uppercase tracking-wide">
          System Evaluation
        </h5>
        <span className={cn(
          "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold",
          proposal.system_recommendation === "approve"
            ? "bg-green-100 text-green-800"
            : "bg-red-100 text-red-800",
        )}>
          {proposal.system_recommendation === "approve" ? "Recommended" : "Not Recommended"}
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Compliance Impact</dt>
        <dd className={cn("font-medium capitalize rounded px-1", impactColor)}>{eval_.compliance_impact}</dd>

        <dt className="text-muted-foreground">Risk</dt>
        <dd className={cn("font-medium capitalize", riskColor)}>{eval_.risk}</dd>

        <dt className="text-muted-foreground">Scope</dt>
        <dd className="font-medium">{eval_.scope === "global_rule" ? "Global" : "Single Document"}</dd>

        <dt className="text-muted-foreground">Reversible</dt>
        <dd className="font-medium">{eval_.reversibility ? "Yes" : "No"}</dd>
      </dl>

      <p className="text-xs text-muted-foreground italic">{eval_.reason}</p>
    </div>
  );
}
