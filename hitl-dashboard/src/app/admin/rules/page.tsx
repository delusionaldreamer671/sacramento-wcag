"use client";

import { useEffect, useState, useMemo } from "react";
import {
  ChevronDown,
  ChevronRight,
  Search,
  Shield,
  BookOpen,
  Zap,
  Eye,
  AlertTriangle,
  FileText,
  CheckCircle2,
  XCircle,
  Clock,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { fetchWCAGRules } from "@/lib/api";
import type { WCAGRuleRef, TechniqueRef, FailureTechniqueRef } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types & Constants
// ---------------------------------------------------------------------------

type LevelFilter = "A" | "AA" | "all";
type ApplicabilityFilter = "always" | "conditional" | "never" | "all";

const PRINCIPLES = ["perceivable", "operable", "understandable", "robust"] as const;
type Principle = (typeof PRINCIPLES)[number];

const PRINCIPLE_LABELS: Record<Principle, string> = {
  perceivable: "Perceivable",
  operable: "Operable",
  understandable: "Understandable",
  robust: "Robust",
};

const PRINCIPLE_DESCRIPTIONS: Record<Principle, string> = {
  perceivable: "Information and UI components must be presentable in ways users can perceive.",
  operable: "UI components and navigation must be operable by all users.",
  understandable: "Information and the operation of UI must be understandable.",
  robust: "Content must be robust enough to be interpreted by current and future assistive technologies.",
};

const PRINCIPLE_ICONS: Record<Principle, React.ReactNode> = {
  perceivable: <Eye size={16} aria-hidden="true" />,
  operable: <Zap size={16} aria-hidden="true" />,
  understandable: <BookOpen size={16} aria-hidden="true" />,
  robust: <Shield size={16} aria-hidden="true" />,
};

const PRINCIPLE_COLORS: Record<Principle, string> = {
  perceivable: "bg-blue-50 text-blue-800 border-blue-200",
  operable: "bg-violet-50 text-violet-800 border-violet-200",
  understandable: "bg-emerald-50 text-emerald-800 border-emerald-200",
  robust: "bg-amber-50 text-amber-800 border-amber-200",
};

const PRINCIPLE_HEADER_COLORS: Record<Principle, string> = {
  perceivable: "bg-blue-50 border-blue-200",
  operable: "bg-violet-50 border-violet-200",
  understandable: "bg-emerald-50 border-emerald-200",
  robust: "bg-amber-50 border-amber-200",
};

// ---------------------------------------------------------------------------
// Badge helpers
// ---------------------------------------------------------------------------

function LevelBadge({ level }: { level: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold",
        level === "AA"
          ? "border-sac-navy/30 bg-sac-navy text-white"
          : "border-sac-blue/40 bg-sac-light text-sac-navy",
      )}
    >
      {level}
    </span>
  );
}

function ApplicabilityBadge({ applicability }: { applicability: string }) {
  const configs: Record<string, { label: string; className: string }> = {
    always: {
      label: "Always",
      className: "border-emerald-200 bg-emerald-50 text-emerald-800",
    },
    conditional: {
      label: "Conditional",
      className: "border-amber-200 bg-amber-50 text-amber-800",
    },
    never: {
      label: "N/A for PDF",
      className: "border-slate-200 bg-slate-50 text-slate-600",
    },
  };
  const config = configs[applicability] ?? {
    label: applicability,
    className: "border-slate-200 bg-slate-50 text-slate-600",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
        config.className,
      )}
    >
      {config.label}
    </span>
  );
}

function AutomationBadge({ automation }: { automation: string }) {
  const configs: Record<string, { label: string; className: string; icon: React.ReactNode }> = {
    automated: {
      label: "Automated",
      className: "border-green-200 bg-green-50 text-green-800",
      icon: <CheckCircle2 size={10} aria-hidden="true" />,
    },
    semi_automated: {
      label: "Semi-Auto",
      className: "border-blue-200 bg-blue-50 text-blue-800",
      icon: <Clock size={10} aria-hidden="true" />,
    },
    manual: {
      label: "Manual",
      className: "border-orange-200 bg-orange-50 text-orange-800",
      icon: <Eye size={10} aria-hidden="true" />,
    },
  };
  const config = configs[automation] ?? {
    label: automation,
    className: "border-slate-200 bg-slate-50 text-slate-600",
    icon: null,
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
        config.className,
      )}
    >
      {config.icon}
      {config.label}
    </span>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const configs: Record<string, { label: string; className: string }> = {
    critical: {
      label: "Critical",
      className: "border-red-200 bg-red-50 text-red-800",
    },
    serious: {
      label: "Serious",
      className: "border-orange-200 bg-orange-50 text-orange-800",
    },
    moderate: {
      label: "Moderate",
      className: "border-yellow-200 bg-yellow-50 text-yellow-800",
    },
    minor: {
      label: "Minor",
      className: "border-slate-200 bg-slate-50 text-slate-600",
    },
  };
  const config = configs[severity] ?? {
    label: severity,
    className: "border-slate-200 bg-slate-50 text-slate-600",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
        config.className,
      )}
    >
      {config.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Technique card
// ---------------------------------------------------------------------------

function TechniqueCard({ technique }: { technique: TechniqueRef }) {
  return (
    <div className="rounded-md border border-border bg-background p-3 space-y-1.5">
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-xs font-semibold text-sac-navy">{technique.id}</span>
        <span className="inline-flex items-center rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-xs text-blue-700 capitalize">
          {technique.technique_type.replace(/_/g, " ")}
        </span>
      </div>
      <p className="text-xs font-medium text-foreground leading-snug">{technique.title}</p>
      {technique.pdf_structure && (
        <p className="text-xs text-muted-foreground">
          <span className="font-medium">PDF structure:</span> {technique.pdf_structure}
        </p>
      )}
      {technique.check_description && (
        <p className="text-xs text-muted-foreground leading-relaxed">{technique.check_description}</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Failure technique card
// ---------------------------------------------------------------------------

function FailureTechniqueCard({ failure }: { failure: FailureTechniqueRef }) {
  return (
    <div className="rounded-md border border-red-200 bg-red-50/60 p-3 space-y-1.5">
      <div className="flex items-start gap-2">
        <XCircle size={12} className="mt-0.5 shrink-0 text-red-500" aria-hidden="true" />
        <span className="font-mono text-xs font-semibold text-red-700">{failure.id}</span>
      </div>
      <p className="text-xs font-medium text-foreground leading-snug">{failure.title}</p>
      {failure.description && (
        <p className="text-xs text-muted-foreground leading-relaxed">{failure.description}</p>
      )}
      {failure.pdf_implication && (
        <p className="text-xs text-red-700">
          <span className="font-medium">PDF implication:</span> {failure.pdf_implication}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Collapsible rule row
// ---------------------------------------------------------------------------

function RuleRow({ rule }: { rule: WCAGRuleRef }) {
  const [expanded, setExpanded] = useState(false);

  const hasTechniques = rule.pdf_techniques.length > 0;
  const hasFailures = rule.failure_techniques.length > 0;
  const hasCondition = Boolean(rule.condition);
  const hasDetails = hasTechniques || hasFailures || hasCondition || Boolean(rule.description);

  return (
    <div className="rounded-lg border border-border bg-card shadow-sac overflow-hidden">
      {/* Summary row — always visible */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className={cn(
          "w-full flex items-center gap-3 px-4 py-3 text-left",
          "hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
          "transition-colors",
          expanded && "border-b border-border",
        )}
      >
        {/* Expand icon */}
        <span className="shrink-0 text-muted-foreground">
          {expanded ? (
            <ChevronDown size={14} aria-hidden="true" />
          ) : (
            <ChevronRight size={14} aria-hidden="true" />
          )}
        </span>

        {/* Criterion number */}
        <span className="shrink-0 font-mono text-sm font-bold text-sac-navy w-10">
          {rule.criterion}
        </span>

        {/* Name */}
        <span className="flex-1 text-sm font-medium text-foreground min-w-0 truncate">
          {rule.name}
        </span>

        {/* Badges — right side */}
        <span className="hidden sm:flex shrink-0 items-center gap-1.5 flex-wrap justify-end">
          <LevelBadge level={rule.level} />
          <ApplicabilityBadge applicability={rule.pdf_applicability} />
          <AutomationBadge automation={rule.automation} />
          <SeverityBadge severity={rule.default_severity} />
        </span>
      </button>

      {/* Mobile badges — shown when collapsed on small screens */}
      <div className="sm:hidden flex flex-wrap gap-1.5 px-4 pb-2.5">
        <LevelBadge level={rule.level} />
        <ApplicabilityBadge applicability={rule.pdf_applicability} />
        <AutomationBadge automation={rule.automation} />
        <SeverityBadge severity={rule.default_severity} />
      </div>

      {/* Expanded detail */}
      {expanded && hasDetails && (
        <div className="px-4 py-4 space-y-4">
          {/* Description */}
          {rule.description && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
                Description
              </p>
              <p className="text-sm text-foreground leading-relaxed">{rule.description}</p>
            </div>
          )}

          {/* Condition */}
          {hasCondition && (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-amber-700 mb-1">
                Condition (PDF applicability)
              </p>
              <p className="text-xs text-amber-900 leading-relaxed">{rule.condition}</p>
            </div>
          )}

          {/* Meta row */}
          <div className="flex flex-wrap gap-4 text-xs text-muted-foreground border-t border-border pt-3">
            <span>
              <span className="font-medium text-foreground">Guideline:</span> {rule.guideline}
            </span>
            <span>
              <span className="font-medium text-foreground">Default remediation:</span>{" "}
              {rule.default_remediation.replace(/_/g, " ")}
            </span>
          </div>

          {/* Techniques */}
          {hasTechniques && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                PDF Techniques ({rule.pdf_techniques.length})
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {rule.pdf_techniques.map((t) => (
                  <TechniqueCard key={t.id} technique={t} />
                ))}
              </div>
            </div>
          )}

          {/* Failure techniques */}
          {hasFailures && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-red-600 mb-2 flex items-center gap-1">
                <AlertTriangle size={11} aria-hidden="true" />
                Failure Techniques ({rule.failure_techniques.length})
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {rule.failure_techniques.map((f) => (
                  <FailureTechniqueCard key={f.id} failure={f} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {expanded && !hasDetails && (
        <div className="px-4 py-3">
          <p className="text-xs text-muted-foreground italic">No additional details available for this criterion.</p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Principle section
// ---------------------------------------------------------------------------

function PrincipleSection({
  principle,
  rules,
}: {
  principle: Principle;
  rules: WCAGRuleRef[];
}) {
  const [collapsed, setCollapsed] = useState(false);

  if (rules.length === 0) return null;

  return (
    <section aria-labelledby={`principle-${principle}`}>
      {/* Section header */}
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        aria-expanded={!collapsed}
        className={cn(
          "w-full flex items-center justify-between gap-3 rounded-lg border px-4 py-3 mb-3",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          "transition-colors hover:brightness-95",
          PRINCIPLE_HEADER_COLORS[principle],
        )}
      >
        <div className="flex items-center gap-2.5">
          <span
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-md border",
              PRINCIPLE_COLORS[principle],
            )}
          >
            {PRINCIPLE_ICONS[principle]}
          </span>
          <div className="text-left">
            <h2
              id={`principle-${principle}`}
              className="text-sm font-bold text-foreground"
            >
              {PRINCIPLE_LABELS[principle]}
            </h2>
            <p className="hidden sm:block text-xs text-muted-foreground">
              {PRINCIPLE_DESCRIPTIONS[principle]}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="rounded-full border border-border bg-card px-2 py-0.5 text-xs font-semibold text-muted-foreground">
            {rules.length} {rules.length === 1 ? "criterion" : "criteria"}
          </span>
          {collapsed ? (
            <ChevronRight size={14} className="text-muted-foreground" aria-hidden="true" />
          ) : (
            <ChevronDown size={14} className="text-muted-foreground" aria-hidden="true" />
          )}
        </div>
      </button>

      {/* Rules list */}
      {!collapsed && (
        <div className="space-y-2 mb-6">
          {rules.map((rule) => (
            <RuleRow key={rule.criterion} rule={rule} />
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function WCAGRulesBrowserPage() {
  const [rules, setRules] = useState<WCAGRuleRef[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [levelFilter, setLevelFilter] = useState<LevelFilter>("all");
  const [applicabilityFilter, setApplicabilityFilter] = useState<ApplicabilityFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchWCAGRules()
      .then(setRules)
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load WCAG rules.");
      })
      .finally(() => setLoading(false));
  }, []);

  // Filter and group
  const grouped = useMemo<Record<Principle, WCAGRuleRef[]>>(() => {
    const q = searchQuery.trim().toLowerCase();

    const filtered = rules.filter((rule) => {
      if (levelFilter !== "all" && rule.level !== levelFilter) return false;
      if (applicabilityFilter !== "all" && rule.pdf_applicability !== applicabilityFilter) return false;
      if (q) {
        const haystack = [
          rule.criterion,
          rule.name,
          rule.description,
          rule.guideline,
          rule.condition,
          ...rule.pdf_techniques.map((t) => `${t.id} ${t.title}`),
          ...rule.failure_techniques.map((f) => `${f.id} ${f.title}`),
        ]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });

    const result: Record<Principle, WCAGRuleRef[]> = {
      perceivable: [],
      operable: [],
      understandable: [],
      robust: [],
    };
    for (const rule of filtered) {
      const p = rule.principle as Principle;
      if (p in result) {
        result[p].push(rule);
      }
    }
    // Sort each group by criterion number
    for (const p of PRINCIPLES) {
      result[p].sort((a, b) => {
        const aParts = a.criterion.split(".").map(Number);
        const bParts = b.criterion.split(".").map(Number);
        for (let i = 0; i < Math.max(aParts.length, bParts.length); i++) {
          const diff = (aParts[i] ?? 0) - (bParts[i] ?? 0);
          if (diff !== 0) return diff;
        }
        return 0;
      });
    }
    return result;
  }, [rules, levelFilter, applicabilityFilter, searchQuery]);

  const totalVisible = PRINCIPLES.reduce((acc, p) => acc + grouped[p].length, 0);

  return (
    <div className="container mx-auto max-w-screen-xl px-4 py-6 sm:px-6 space-y-6">
      {/* Header */}
      <header className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-sac-light text-sac-navy">
          <Shield size={20} aria-hidden="true" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-foreground">WCAG 2.1 AA Reference</h1>
          <p className="text-sm text-muted-foreground">
            Browse all success criteria with PDF applicability, automation level, and technique guidance.
          </p>
        </div>
      </header>

      {/* Filter controls */}
      <div
        className="rounded-lg border border-border bg-card p-4 shadow-sac"
        role="search"
        aria-label="Filter WCAG rules"
      >
        <div className="flex flex-col sm:flex-row gap-3">
          {/* Text search */}
          <div className="relative flex-1">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
              aria-hidden="true"
            />
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search criteria, techniques, IDs..."
              aria-label="Search WCAG rules"
              className={cn(
                "w-full rounded-md border border-border bg-background py-2 pl-8 pr-3 text-sm",
                "placeholder:text-muted-foreground",
                "focus:outline-none focus:ring-2 focus:ring-ring",
              )}
            />
          </div>

          {/* Level filter */}
          <fieldset className="flex items-center gap-1">
            <legend className="sr-only">Filter by WCAG level</legend>
            {(["all", "A", "AA"] as LevelFilter[]).map((lvl) => (
              <button
                key={lvl}
                type="button"
                onClick={() => setLevelFilter(lvl)}
                aria-pressed={levelFilter === lvl}
                className={cn(
                  "rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  levelFilter === lvl
                    ? "bg-sac-navy text-white"
                    : "bg-muted text-muted-foreground hover:bg-muted/70",
                )}
              >
                {lvl === "all" ? "All levels" : `Level ${lvl}`}
              </button>
            ))}
          </fieldset>

          {/* Applicability filter */}
          <fieldset className="flex items-center gap-1">
            <legend className="sr-only">Filter by PDF applicability</legend>
            {(["all", "always", "conditional", "never"] as ApplicabilityFilter[]).map((app) => (
              <button
                key={app}
                type="button"
                onClick={() => setApplicabilityFilter(app)}
                aria-pressed={applicabilityFilter === app}
                className={cn(
                  "rounded-md px-3 py-2 text-xs font-medium capitalize transition-colors",
                  applicabilityFilter === app
                    ? "bg-sac-navy text-white"
                    : "bg-muted text-muted-foreground hover:bg-muted/70",
                )}
              >
                {app === "all" ? "All PDF" : app === "never" ? "N/A for PDF" : app}
              </button>
            ))}
          </fieldset>
        </div>

        {/* Results summary */}
        {!loading && !error && (
          <p className="mt-2.5 text-xs text-muted-foreground">
            {totalVisible === 0
              ? "No criteria match the current filters."
              : `Showing ${totalVisible} of ${rules.length} criteria`}
          </p>
        )}
      </div>

      {/* Loading */}
      {loading && (
        <div
          role="status"
          aria-label="Loading WCAG rules"
          className="flex flex-col items-center justify-center rounded-lg border border-border bg-card py-16 shadow-sac"
        >
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-sac-navy/20 border-t-sac-navy mb-2" />
          <p className="text-sm text-muted-foreground">Loading WCAG rules...</p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div
          role="alert"
          className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4"
        >
          <AlertTriangle size={18} className="shrink-0 mt-0.5 text-red-600" aria-hidden="true" />
          <div>
            <p className="text-sm font-semibold text-red-800">Failed to load WCAG rules</p>
            <p className="text-xs text-red-700 mt-0.5">{error}</p>
            <button
              type="button"
              onClick={() => {
                setLoading(true);
                setError(null);
                fetchWCAGRules()
                  .then(setRules)
                  .catch((err) => setError(err instanceof Error ? err.message : "Failed to load WCAG rules."))
                  .finally(() => setLoading(false));
              }}
              className="mt-2 text-xs font-medium text-red-800 underline hover:no-underline"
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && totalVisible === 0 && rules.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-12 text-center shadow-sac">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-muted">
            <FileText size={24} className="text-muted-foreground" aria-hidden="true" />
          </div>
          <p className="mt-4 font-semibold text-foreground">No criteria match</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Try adjusting the search query or filters.
          </p>
        </div>
      )}

      {/* Grouped by principle */}
      {!loading && !error && totalVisible > 0 && (
        <div>
          {PRINCIPLES.map((principle) => (
            <PrincipleSection
              key={principle}
              principle={principle}
              rules={grouped[principle]}
            />
          ))}
        </div>
      )}
    </div>
  );
}
