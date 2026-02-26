"use client";

import { useEffect, useState, useMemo } from "react";
import { cn } from "@/lib/utils";
import {
  fetchCoverageMatrix,
  fetchCoverageSummary,
  fetchContentTypeMatrix,
} from "@/lib/api";
import type {
  CoverageMatrixEntry,
  CoverageSummary,
  ContentTypeEntry,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type TabId = "criteria" | "content-types";
type LevelFilter = "A" | "AA" | "all";
type AutomationFilter = "automated" | "semi_automated" | "manual" | "all";
type ApplicabilityFilter = "always" | "conditional" | "never" | "all";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const AUTOMATION_LABELS: Record<string, string> = {
  automated: "Automated",
  semi_automated: "Semi-Automated",
  manual: "Manual",
};

const AUTOMATION_COLORS: Record<string, string> = {
  automated: "bg-green-100 text-green-800 border-green-200",
  semi_automated: "bg-amber-100 text-amber-800 border-amber-200",
  manual: "bg-red-100 text-red-800 border-red-200",
};

const APPLICABILITY_LABELS: Record<string, string> = {
  always: "Always",
  conditional: "Conditional",
  never: "Never",
};

const SEVERITY_COLORS: Record<string, string> = {
  critical: "bg-red-100 text-red-800",
  serious: "bg-orange-100 text-orange-800",
  moderate: "bg-amber-100 text-amber-800",
  minor: "bg-blue-100 text-blue-800",
};

const REMEDIATION_LABELS: Record<string, string> = {
  auto_fix: "Auto Fix",
  ai_draft: "AI Draft",
  manual_review: "Manual Review",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CoveragePage() {
  const [activeTab, setActiveTab] = useState<TabId>("criteria");
  const [matrix, setMatrix] = useState<CoverageMatrixEntry[]>([]);
  const [summary, setSummary] = useState<CoverageSummary | null>(null);
  const [contentTypes, setContentTypes] = useState<ContentTypeEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters for criteria tab
  const [levelFilter, setLevelFilter] = useState<LevelFilter>("all");
  const [automationFilter, setAutomationFilter] = useState<AutomationFilter>("all");
  const [applicabilityFilter, setApplicabilityFilter] = useState<ApplicabilityFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");

  // Expanded rows
  const [expandedCriteria, setExpandedCriteria] = useState<Set<string>>(new Set());
  const [expandedContentTypes, setExpandedContentTypes] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        setLoading(true);
        const [matrixData, summaryData, ctData] = await Promise.all([
          fetchCoverageMatrix(),
          fetchCoverageSummary(),
          fetchContentTypeMatrix(),
        ]);
        if (cancelled) return;
        setMatrix(matrixData);
        setSummary(summaryData);
        setContentTypes(ctData);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load coverage data");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => { cancelled = true; };
  }, []);

  const filteredMatrix = useMemo(() => {
    return matrix.filter((entry) => {
      if (levelFilter !== "all" && entry.level !== levelFilter) return false;
      if (automationFilter !== "all" && entry.automation !== automationFilter) return false;
      if (applicabilityFilter !== "all" && entry.pdf_applicability !== applicabilityFilter) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        if (
          !entry.criterion.toLowerCase().includes(q) &&
          !entry.name.toLowerCase().includes(q) &&
          !entry.description.toLowerCase().includes(q)
        ) {
          return false;
        }
      }
      return true;
    });
  }, [matrix, levelFilter, automationFilter, applicabilityFilter, searchQuery]);

  function toggleCriterion(criterion: string) {
    setExpandedCriteria((prev) => {
      const next = new Set(prev);
      if (next.has(criterion)) {
        next.delete(criterion);
      } else {
        next.add(criterion);
      }
      return next;
    });
  }

  function toggleContentType(ct: string) {
    setExpandedContentTypes((prev) => {
      const next = new Set(prev);
      if (next.has(ct)) {
        next.delete(ct);
      } else {
        next.add(ct);
      }
      return next;
    });
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-blue-600" />
          <p className="text-sm text-muted-foreground">Loading coverage data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-center">
          <p className="font-medium text-red-800">Failed to load coverage data</p>
          <p className="mt-1 text-sm text-red-600">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight">WCAG 2.1 AA Coverage Matrix</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          50 success criteria with automation levels, PDF techniques, and content-type breakdown.
        </p>
      </header>

      {/* Summary cards */}
      {summary && <SummaryCards summary={summary} />}

      {/* Tabs */}
      <div className="mt-8 border-b border-gray-200" role="tablist" aria-label="Coverage views">
        <nav className="-mb-px flex gap-4">
          {(["criteria", "content-types"] as const).map((tab) => (
            <button
              key={tab}
              role="tab"
              aria-selected={activeTab === tab}
              aria-controls={`panel-${tab}`}
              onClick={() => setActiveTab(tab)}
              className={cn(
                "whitespace-nowrap border-b-2 px-1 pb-3 text-sm font-medium transition-colors",
                activeTab === tab
                  ? "border-blue-600 text-blue-600"
                  : "border-transparent text-muted-foreground hover:border-gray-300 hover:text-foreground",
              )}
            >
              {tab === "criteria" ? "By Criterion" : "By Content Type"}
            </button>
          ))}
        </nav>
      </div>

      {/* Criteria Tab */}
      {activeTab === "criteria" && (
        <div id="panel-criteria" role="tabpanel" className="mt-6">
          {/* Filters */}
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <input
              type="text"
              placeholder="Search criteria..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              aria-label="Search criteria"
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <FilterSelect
              label="Level"
              value={levelFilter}
              onChange={(v) => setLevelFilter(v as LevelFilter)}
              options={[
                { value: "all", label: "All Levels" },
                { value: "A", label: "Level A" },
                { value: "AA", label: "Level AA" },
              ]}
            />
            <FilterSelect
              label="Automation"
              value={automationFilter}
              onChange={(v) => setAutomationFilter(v as AutomationFilter)}
              options={[
                { value: "all", label: "All" },
                { value: "automated", label: "Automated" },
                { value: "semi_automated", label: "Semi-Auto" },
                { value: "manual", label: "Manual" },
              ]}
            />
            <FilterSelect
              label="PDF Applicability"
              value={applicabilityFilter}
              onChange={(v) => setApplicabilityFilter(v as ApplicabilityFilter)}
              options={[
                { value: "all", label: "All" },
                { value: "always", label: "Always" },
                { value: "conditional", label: "Conditional" },
                { value: "never", label: "Never" },
              ]}
            />
            <span className="ml-auto text-xs text-muted-foreground">
              {filteredMatrix.length} of {matrix.length} criteria
            </span>
          </div>

          {/* Criteria table */}
          <div className="overflow-x-auto rounded-lg border">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th scope="col" className="px-3 py-2 text-left font-medium text-gray-600">Criterion</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium text-gray-600">Name</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium text-gray-600">Level</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium text-gray-600">PDF</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium text-gray-600">Automation</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium text-gray-600">Severity</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium text-gray-600">Remediation</th>
                  <th scope="col" className="px-3 py-2 text-center font-medium text-gray-600">Techniques</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {filteredMatrix.map((entry) => {
                  const isExpanded = expandedCriteria.has(entry.criterion);
                  return (
                    <CriterionRow
                      key={entry.criterion}
                      entry={entry}
                      isExpanded={isExpanded}
                      onToggle={() => toggleCriterion(entry.criterion)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Content Types Tab */}
      {activeTab === "content-types" && (
        <div id="panel-content-types" role="tabpanel" className="mt-6 space-y-4">
          {contentTypes.map((ct) => {
            const isExpanded = expandedContentTypes.has(ct.content_type);
            return (
              <ContentTypeCard
                key={ct.content_type}
                entry={ct}
                isExpanded={isExpanded}
                onToggle={() => toggleContentType(ct.content_type)}
              />
            );
          })}
        </div>
      )}
    </main>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SummaryCards({ summary }: { summary: CoverageSummary }) {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      <SummaryCard label="Total Criteria" value={String(summary.total_criteria)} />
      <SummaryCard
        label="Automated"
        value={String(summary.by_automation.automated ?? 0)}
        subtext={`of ${summary.total_criteria}`}
        color="text-green-700"
      />
      <SummaryCard
        label="Semi-Automated"
        value={String(summary.by_automation.semi_automated ?? 0)}
        subtext={`of ${summary.total_criteria}`}
        color="text-amber-700"
      />
      <SummaryCard
        label="Manual Only"
        value={String(summary.by_automation.manual ?? 0)}
        subtext={`of ${summary.total_criteria}`}
        color="text-red-700"
      />
    </div>
  );
}

function SummaryCard({
  label,
  value,
  subtext,
  color,
}: {
  label: string;
  value: string;
  subtext?: string;
  color?: string;
}) {
  return (
    <div className="rounded-lg border bg-white p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn("mt-1 text-2xl font-bold", color)}>{value}</p>
      {subtext && <p className="text-xs text-muted-foreground">{subtext}</p>}
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: Array<{ value: string; label: string }>;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}

function CriterionRow({
  entry,
  isExpanded,
  onToggle,
}: {
  entry: CoverageMatrixEntry;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const techCount = entry.pdf_techniques.length + entry.failure_techniques.length;

  return (
    <>
      <tr
        className="cursor-pointer hover:bg-gray-50 transition-colors"
        onClick={onToggle}
        aria-expanded={isExpanded}
      >
        <td className="whitespace-nowrap px-3 py-2 font-mono font-medium">{entry.criterion}</td>
        <td className="px-3 py-2">{entry.name}</td>
        <td className="px-3 py-2">
          <span className={cn(
            "inline-block rounded px-1.5 py-0.5 text-xs font-medium",
            entry.level === "A" ? "bg-blue-100 text-blue-800" : "bg-purple-100 text-purple-800",
          )}>
            {entry.level}
          </span>
        </td>
        <td className="px-3 py-2">
          <span className="text-xs">{APPLICABILITY_LABELS[entry.pdf_applicability] ?? entry.pdf_applicability}</span>
        </td>
        <td className="px-3 py-2">
          <span className={cn(
            "inline-block rounded border px-1.5 py-0.5 text-xs font-medium",
            AUTOMATION_COLORS[entry.automation] ?? "",
          )}>
            {AUTOMATION_LABELS[entry.automation] ?? entry.automation}
          </span>
        </td>
        <td className="px-3 py-2">
          <span className={cn(
            "inline-block rounded px-1.5 py-0.5 text-xs",
            SEVERITY_COLORS[entry.default_severity] ?? "",
          )}>
            {entry.default_severity}
          </span>
        </td>
        <td className="px-3 py-2 text-xs">
          {REMEDIATION_LABELS[entry.default_remediation] ?? entry.default_remediation}
        </td>
        <td className="px-3 py-2 text-center text-xs text-muted-foreground">
          {techCount > 0 ? techCount : "-"}
        </td>
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={8} className="bg-gray-50/70 px-6 py-4">
            <div className="space-y-3 text-sm">
              <p className="text-gray-700">{entry.description}</p>
              {entry.condition && (
                <p className="text-xs text-amber-700">
                  <strong>Condition:</strong> {entry.condition}
                </p>
              )}
              {entry.pdf_techniques.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-medium text-gray-600">PDF Techniques:</p>
                  <ul className="list-disc space-y-1 pl-4 text-xs text-gray-600">
                    {entry.pdf_techniques.map((t) => (
                      <li key={t.id}>
                        <strong>{t.id}</strong>: {t.title} — <span className="italic">{t.check_description}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {entry.failure_techniques.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-medium text-red-600">Failure Techniques:</p>
                  <ul className="list-disc space-y-1 pl-4 text-xs text-red-600">
                    {entry.failure_techniques.map((f) => (
                      <li key={f.id}>
                        <strong>{f.id}</strong>: {f.title} — <span className="italic">{f.pdf_implication}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function ContentTypeCard({
  entry,
  isExpanded,
  onToggle,
}: {
  entry: ContentTypeEntry;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const total = entry.automated_count + entry.ai_assisted_count + entry.human_review_count;
  const autoPct = total > 0 ? Math.round((entry.automated_count / total) * 100) : 0;
  const aiPct = total > 0 ? Math.round((entry.ai_assisted_count / total) * 100) : 0;
  const humanPct = total > 0 ? 100 - autoPct - aiPct : 0;

  return (
    <div className="rounded-lg border bg-white">
      <button
        type="button"
        className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-gray-50 transition-colors"
        onClick={onToggle}
        aria-expanded={isExpanded}
      >
        <div>
          <h3 className="font-medium">{entry.content_type}</h3>
          <p className="text-xs text-muted-foreground">{entry.description}</p>
        </div>
        <div className="flex items-center gap-4">
          {/* Mini bar chart */}
          <div className="flex items-center gap-1">
            <div className="flex h-3 w-24 overflow-hidden rounded-full bg-gray-200">
              {autoPct > 0 && (
                <div
                  className="bg-green-500"
                  style={{ width: `${autoPct}%` }}
                  title={`Automated: ${autoPct}%`}
                />
              )}
              {aiPct > 0 && (
                <div
                  className="bg-amber-400"
                  style={{ width: `${aiPct}%` }}
                  title={`AI-Assisted: ${aiPct}%`}
                />
              )}
              {humanPct > 0 && (
                <div
                  className="bg-red-400"
                  style={{ width: `${humanPct}%` }}
                  title={`Human: ${humanPct}%`}
                />
              )}
            </div>
            <span className="text-xs text-muted-foreground">{total} actions</span>
          </div>
          <span className="text-muted-foreground">
            {isExpanded ? "\u25B2" : "\u25BC"}
          </span>
        </div>
      </button>

      {isExpanded && (
        <div className="border-t px-4 py-4 space-y-4">
          <div className="flex gap-2 text-xs">
            <span className="text-muted-foreground">Relevant criteria:</span>
            {entry.relevant_criteria.map((c) => (
              <span key={c} className="rounded bg-gray-100 px-1.5 py-0.5 font-mono">{c}</span>
            ))}
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <ActionList
              title="Automated"
              items={entry.automated_actions}
              colorClass="border-green-200 bg-green-50"
              titleColor="text-green-800"
            />
            <ActionList
              title="AI-Assisted"
              items={entry.ai_assisted_actions}
              colorClass="border-amber-200 bg-amber-50"
              titleColor="text-amber-800"
            />
            <ActionList
              title="Human Review"
              items={entry.human_review_actions}
              colorClass="border-red-200 bg-red-50"
              titleColor="text-red-800"
            />
          </div>
        </div>
      )}
    </div>
  );
}

function ActionList({
  title,
  items,
  colorClass,
  titleColor,
}: {
  title: string;
  items: string[];
  colorClass: string;
  titleColor: string;
}) {
  return (
    <div className={cn("rounded-md border p-3", colorClass)}>
      <p className={cn("mb-2 text-xs font-medium", titleColor)}>
        {title} ({items.length})
      </p>
      {items.length > 0 ? (
        <ul className="space-y-1 text-xs">
          {items.map((item, i) => (
            <li key={i} className="flex gap-1.5">
              <span className="mt-0.5 shrink-0">&#8226;</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs italic text-muted-foreground">None</p>
      )}
    </div>
  );
}
