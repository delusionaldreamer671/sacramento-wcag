"use client";

import { useMemo, useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Search,
  FileText,
  Wrench,
  Eye,
  ImageOff,
  Heading,
  Table2,
  Languages,
  ArrowDownUp,
  Shield,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAnalysisResult } from "@/lib/analysis-store";
import type { AnalysisProposal } from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function severityColor(severity: string): string {
  switch (severity) {
    case "critical": return "bg-red-100 text-red-800 border-red-200";
    case "serious": return "bg-orange-100 text-orange-800 border-orange-200";
    case "moderate": return "bg-yellow-100 text-yellow-800 border-yellow-200";
    case "minor": return "bg-blue-100 text-blue-800 border-blue-200";
    default: return "bg-gray-100 text-gray-800 border-gray-200";
  }
}

function actionBadge(action: string, autoFixable: boolean) {
  if (action === "auto_fix" || (!action && autoFixable)) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-green-50 border border-green-200 px-2 py-0.5 text-[10px] font-medium text-green-700">
        <Wrench className="h-2.5 w-2.5" aria-hidden="true" />
        Auto-fix
      </span>
    );
  }
  if (action === "ai_draft") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-purple-50 border border-purple-200 px-2 py-0.5 text-[10px] font-medium text-purple-700">
        <Search className="h-2.5 w-2.5" aria-hidden="true" />
        AI Draft
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200 px-2 py-0.5 text-[10px] font-medium text-amber-700">
      <Eye className="h-2.5 w-2.5" aria-hidden="true" />
      Needs Review
    </span>
  );
}

function categoryIcon(cat: string) {
  if (cat.startsWith("1.1")) return <ImageOff className="h-4 w-4" aria-hidden="true" />;
  if (cat.startsWith("1.3")) return <Table2 className="h-4 w-4" aria-hidden="true" />;
  if (cat.startsWith("2.4") || cat.startsWith("1.3.2")) return <ArrowDownUp className="h-4 w-4" aria-hidden="true" />;
  if (cat.startsWith("3.1")) return <Languages className="h-4 w-4" aria-hidden="true" />;
  if (cat.startsWith("2.")) return <Shield className="h-4 w-4" aria-hidden="true" />;
  if (cat.includes("heading") || cat.startsWith("2.4.6")) return <Heading className="h-4 w-4" aria-hidden="true" />;
  return <Wrench className="h-4 w-4" aria-hidden="true" />;
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type SeverityFilter = "all" | "critical" | "serious" | "moderate" | "minor";
type ActionFilter = "all" | "auto_fix" | "ai_draft" | "manual_review";

export default function IssuesPage() {
  const analysis = useAnalysisResult();
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>("all");
  const [actionFilter, setActionFilter] = useState<ActionFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [expandedCriteria, setExpandedCriteria] = useState<Set<string>>(new Set());

  // Filter proposals
  const filtered = useMemo(() => {
    if (!analysis) return [];
    let list = analysis.proposals;

    if (severityFilter !== "all") {
      list = list.filter((p) => p.severity === severityFilter);
    }
    if (actionFilter !== "all") {
      list = list.filter((p) => p.action_type === actionFilter);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter(
        (p) =>
          p.description.toLowerCase().includes(q) ||
          p.proposed_fix.toLowerCase().includes(q) ||
          p.wcag_criterion.includes(q) ||
          (p.rule_name ?? "").toLowerCase().includes(q),
      );
    }
    return list;
  }, [analysis, severityFilter, actionFilter, searchQuery]);

  // Group by WCAG criterion
  const grouped = useMemo(() => {
    const map = new Map<string, { name: string; proposals: AnalysisProposal[] }>();
    for (const p of filtered) {
      if (!map.has(p.wcag_criterion)) {
        map.set(p.wcag_criterion, { name: p.rule_name ?? p.wcag_criterion, proposals: [] });
      }
      map.get(p.wcag_criterion)!.proposals.push(p);
    }
    // Sort by criterion number
    return Array.from(map.entries()).sort(([a], [b]) => {
      const aParts = a.split(".").map(Number);
      const bParts = b.split(".").map(Number);
      for (let i = 0; i < Math.max(aParts.length, bParts.length); i++) {
        const diff = (aParts[i] ?? 0) - (bParts[i] ?? 0);
        if (diff !== 0) return diff;
      }
      return 0;
    });
  }, [filtered]);

  const toggleCriterion = (criterion: string) => {
    setExpandedCriteria((prev) => {
      const next = new Set(prev);
      if (next.has(criterion)) next.delete(criterion);
      else next.add(criterion);
      return next;
    });
  };

  // Empty state — no analysis run yet
  if (!analysis) {
    return (
      <div className="container mx-auto max-w-screen-xl px-4 py-8 sm:px-6 space-y-6">
        <header className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-100 text-amber-700">
            <AlertCircle className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-foreground">Issues</h1>
            <p className="text-sm text-muted-foreground">
              WCAG accessibility findings from your latest analysis
            </p>
          </div>
        </header>
        <div className="rounded-lg border border-border bg-card p-12 text-center shadow-sac">
          <FileText className="mx-auto h-12 w-12 text-muted-foreground/40" aria-hidden="true" />
          <p className="mt-4 font-semibold text-foreground">No analysis results</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Upload and analyze a PDF on the{" "}
            <a href="/upload" className="text-sac-navy underline hover:text-sac-navy/80">
              Upload page
            </a>{" "}
            to see accessibility issues here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto max-w-screen-xl px-4 py-8 sm:px-6 space-y-6">
      {/* Header */}
      <header className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-100 text-amber-700">
          <AlertCircle className="h-5 w-5" aria-hidden="true" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-foreground">Issues</h1>
          <p className="text-sm text-muted-foreground">
            {analysis.filename} — {filtered.length} issue{filtered.length !== 1 ? "s" : ""}{" "}
            {filtered.length !== analysis.proposals.length && (
              <span className="text-xs">(filtered from {analysis.proposals.length})</span>
            )}
          </p>
        </div>
      </header>

      {/* Summary chips */}
      <div className="flex flex-wrap items-center gap-2" role="status" aria-label="Issue counts">
        <span className="rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-semibold text-red-800">
          {analysis.summary.critical} critical
        </span>
        <span className="rounded-full bg-orange-100 px-2.5 py-0.5 text-xs font-semibold text-orange-800">
          {analysis.summary.serious} serious
        </span>
        <span className="rounded-full bg-yellow-100 px-2.5 py-0.5 text-xs font-semibold text-yellow-800">
          {analysis.summary.moderate} moderate
        </span>
        <span className="rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-semibold text-green-700">
          {analysis.summary.auto_fixable} auto-fixable
        </span>
        <span className="rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-semibold text-amber-700">
          {analysis.summary.needs_review} need review
        </span>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-4">
        {/* Severity filter */}
        <fieldset className="flex items-center gap-1.5">
          <legend className="sr-only">Filter by severity</legend>
          {(["all", "critical", "serious", "moderate", "minor"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSeverityFilter(s)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs capitalize transition-colors",
                severityFilter === s
                  ? "bg-sac-navy text-white"
                  : "bg-muted text-muted-foreground hover:bg-muted/80",
              )}
              aria-pressed={severityFilter === s}
            >
              {s === "all" ? "All severity" : s}
            </button>
          ))}
        </fieldset>

        {/* Action type filter */}
        <fieldset className="flex items-center gap-1.5">
          <legend className="sr-only">Filter by action type</legend>
          {(["all", "auto_fix", "ai_draft", "manual_review"] as const).map((a) => {
            const labels: Record<string, string> = {
              all: "All types",
              auto_fix: "Auto-fix",
              ai_draft: "AI Draft",
              manual_review: "Manual",
            };
            return (
              <button
                key={a}
                type="button"
                onClick={() => setActionFilter(a)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs transition-colors",
                  actionFilter === a
                    ? "bg-sac-navy text-white"
                    : "bg-muted text-muted-foreground hover:bg-muted/80",
                )}
                aria-pressed={actionFilter === a}
              >
                {labels[a]}
              </button>
            );
          })}
        </fieldset>

        {/* Search */}
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input
            type="search"
            placeholder="Search issues..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full rounded-md border border-border bg-background pl-8 pr-3 py-1.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Search issues"
          />
        </div>
      </div>

      {/* Grouped issues */}
      {grouped.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center shadow-sac">
          <p className="font-medium text-foreground">No issues match filters</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Try adjusting your filters or search query.
          </p>
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-card shadow-sac divide-y divide-border">
          {grouped.map(([criterion, group]) => {
            const isExpanded = expandedCriteria.has(criterion);
            return (
              <div key={criterion}>
                <button
                  type="button"
                  onClick={() => toggleCriterion(criterion)}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/30 transition-colors"
                  aria-expanded={isExpanded}
                >
                  {isExpanded ? (
                    <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" aria-hidden="true" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" aria-hidden="true" />
                  )}
                  <span className="text-sac-navy">{categoryIcon(criterion)}</span>
                  <span className="text-xs font-mono font-semibold text-sac-navy">{criterion}</span>
                  <span className="text-sm text-foreground truncate">{group.name}</span>
                  <span className="ml-auto flex items-center gap-2 shrink-0">
                    <span className="rounded-md bg-secondary px-2 py-0.5 text-xs font-medium text-secondary-foreground">
                      {group.proposals.length} issue{group.proposals.length !== 1 ? "s" : ""}
                    </span>
                  </span>
                </button>

                {isExpanded && (
                  <ul className="divide-y divide-border/50 border-t border-border/50 bg-white" role="list">
                    {group.proposals.map((p) => (
                      <li key={p.id} className="px-4 py-3 pl-12 space-y-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase", severityColor(p.severity))}>
                            {p.severity}
                          </span>
                          {actionBadge(p.action_type, p.auto_fixable)}
                          {p.page > 0 && (
                            <span className="text-[10px] text-muted-foreground">Page {p.page}</span>
                          )}
                        </div>
                        <p className="text-sm text-foreground">{p.description}</p>
                        <p className="text-xs text-muted-foreground">{p.proposed_fix}</p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
