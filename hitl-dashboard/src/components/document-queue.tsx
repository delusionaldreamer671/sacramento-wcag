"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import type { DocumentStatus, PDFDocument } from "@/lib/types";
import { fetchDocuments } from "@/lib/api";

interface DocumentQueueProps {
  /** Initial list of documents (e.g., from server component). Re-fetches on mount. */
  initialDocuments?: PDFDocument[];
  className?: string;
}

type SortKey = "status" | "updated_at" | "filename" | "page_count";
type SortDir = "asc" | "desc";

const STATUS_ORDER: Record<DocumentStatus, number> = {
  failed: 0,
  hitl_review: 1,
  ai_drafting: 2,
  extracting: 3,
  queued: 4,
  recompiling: 5,
  approved: 6,
  complete: 7,
};

const STATUS_LABELS: Record<DocumentStatus, string> = {
  queued: "Queued",
  extracting: "Extracting",
  ai_drafting: "AI Drafting",
  hitl_review: "Needs Review",
  approved: "Approved",
  recompiling: "Recompiling",
  complete: "Complete",
  failed: "Failed",
};

const STATUS_COLORS: Record<DocumentStatus, string> = {
  queued: "bg-slate-100 text-slate-700 border-slate-200",
  extracting: "bg-sky-100 text-sky-800 border-sky-200",
  ai_drafting: "bg-violet-100 text-violet-800 border-violet-200",
  hitl_review: "bg-amber-100 text-amber-800 border-amber-200",
  approved: "bg-green-100 text-green-800 border-green-200",
  recompiling: "bg-sky-100 text-sky-800 border-sky-200",
  complete: "bg-green-100 text-green-800 border-green-200",
  failed: "bg-red-100 text-red-800 border-red-200",
};

/**
 * Pending review count is derived from document status for the MVP — in
 * production this should be replaced with an actual count returned by the API
 * (e.g. a `pending_review_count` field on DocumentStatusResponse).
 *
 * The heuristic below (1 review item per 5 pages, minimum 1) is intentionally
 * crude: it serves only to populate the "Pending Reviews" column with a
 * non-zero indicator when a document is in the hitl_review state, so reviewers
 * know work is waiting.  It does NOT reflect the true number of HITLReviewItem
 * records for that document.
 *
 * TODO: Replace with actual review item count from the API once the list
 * endpoint includes per-document pending_review_count.
 */
function getPendingReviewCount(doc: PDFDocument): number {
  if (doc.status !== "hitl_review") return 0;
  // Heuristic: ~1 review item per 5 pages — replace with real API count.
  return doc.page_count > 0 ? Math.max(1, Math.floor(doc.page_count / 5)) : 1;
}

export function DocumentQueue({
  initialDocuments = [],
  className,
}: DocumentQueueProps) {
  const [documents, setDocuments] = useState<PDFDocument[]>(initialDocuments);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("status");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);

  const PAGE_SIZE = 20;
  const announceRef = useRef<HTMLDivElement>(null);

  const load = useCallback(
    async (pageIdx: number) => {
      setIsLoading(true);
      setLoadError(null);
      try {
        const results = await fetchDocuments(pageIdx * PAGE_SIZE, PAGE_SIZE + 1);
        setHasMore(results.length > PAGE_SIZE);
        const pageResults = results.slice(0, PAGE_SIZE);
        setDocuments(pageIdx === 0 ? pageResults : (prev) => [...prev, ...pageResults]);
      } catch (err) {
        setLoadError(
          err instanceof Error ? err.message : "Failed to load documents.",
        );
      } finally {
        setIsLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  // ----------------------------------------------------------------
  // Sorting
  // ----------------------------------------------------------------
  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }

    if (announceRef.current) {
      const dir = key === sortKey ? (sortDir === "asc" ? "descending" : "ascending") : "ascending";
      announceRef.current.textContent = `Table sorted by ${key} ${dir}`;
    }
  }

  const sortedDocuments = [...documents].sort((a, b) => {
    let result = 0;
    switch (sortKey) {
      case "status":
        result = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
        break;
      case "updated_at":
        result =
          new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime();
        break;
      case "filename":
        result = a.filename.localeCompare(b.filename);
        break;
      case "page_count":
        result = a.page_count - b.page_count;
        break;
    }
    return sortDir === "asc" ? result : -result;
  });

  // ----------------------------------------------------------------
  // Render
  // ----------------------------------------------------------------
  if (loadError && documents.length === 0) {
    return (
      <div
        role="alert"
        className={cn(
          "rounded-lg border border-destructive bg-destructive/10 p-6 text-center",
          className,
        )}
      >
        <p className="text-sm font-medium text-destructive">
          Failed to load document queue
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{loadError}</p>
        <button
          onClick={() => void load(0)}
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

  return (
    <div className={cn("space-y-3", className)}>
      {/* Live region for sort announcements */}
      <div
        ref={announceRef}
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
      />

      {isLoading && documents.length === 0 ? (
        <div
          role="status"
          aria-label="Loading documents"
          className="flex min-h-[200px] items-center justify-center"
        >
          <p className="text-sm text-muted-foreground animate-pulse">
            Loading document queue…
          </p>
        </div>
      ) : documents.length === 0 ? (
        <div className="flex min-h-[200px] items-center justify-center rounded-lg border border-dashed border-border">
          <p className="text-sm text-muted-foreground">
            No documents in the queue.
          </p>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table
              className="w-full text-sm"
              aria-label="Document remediation queue"
              aria-rowcount={documents.length}
            >
              <thead>
                <tr className="border-b border-border bg-muted/50">
                  <SortableHeader
                    label="Filename"
                    sortKey="filename"
                    currentKey={sortKey}
                    currentDir={sortDir}
                    onSort={handleSort}
                  />
                  <SortableHeader
                    label="Status"
                    sortKey="status"
                    currentKey={sortKey}
                    currentDir={sortDir}
                    onSort={handleSort}
                  />
                  <SortableHeader
                    label="Pages"
                    sortKey="page_count"
                    currentKey={sortKey}
                    currentDir={sortDir}
                    onSort={handleSort}
                  />
                  <th
                    scope="col"
                    className="px-4 py-3 text-left font-semibold text-foreground"
                  >
                    Pending Reviews
                  </th>
                  <SortableHeader
                    label="Last Updated"
                    sortKey="updated_at"
                    currentKey={sortKey}
                    currentDir={sortDir}
                    onSort={handleSort}
                  />
                  <th
                    scope="col"
                    className="px-4 py-3 text-left font-semibold text-foreground"
                  >
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedDocuments.map((doc, rowIdx) => {
                  const pendingReviews = getPendingReviewCount(doc);
                  return (
                    <tr
                      key={doc.id}
                      aria-rowindex={rowIdx + 2}
                      className={cn(
                        "border-b border-border last:border-0",
                        "hover:bg-muted/30 transition-colors",
                        doc.status === "failed" && "bg-red-50/40",
                        doc.status === "hitl_review" && "bg-amber-50/40",
                      )}
                    >
                      {/* Filename */}
                      <td className="px-4 py-3">
                        <span
                          className="font-medium text-foreground break-all"
                          title={doc.filename}
                        >
                          {doc.filename.length > 40
                            ? `${doc.filename.slice(0, 37)}…`
                            : doc.filename}
                        </span>
                        <span className="block text-xs text-muted-foreground font-mono mt-0.5">
                          {doc.id.slice(0, 8)}…
                        </span>
                      </td>

                      {/* Status badge */}
                      <td className="px-4 py-3">
                        <span
                          className={cn(
                            "status-badge border",
                            STATUS_COLORS[doc.status],
                          )}
                        >
                          {STATUS_LABELS[doc.status]}
                        </span>
                      </td>

                      {/* Page count */}
                      <td className="px-4 py-3 text-muted-foreground">
                        {doc.page_count > 0 ? doc.page_count : "—"}
                      </td>

                      {/* Pending reviews */}
                      <td className="px-4 py-3">
                        {pendingReviews > 0 ? (
                          <span
                            className="font-semibold text-amber-700"
                            aria-label={`${pendingReviews} pending reviews`}
                          >
                            {pendingReviews}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>

                      {/* Last updated */}
                      <td className="px-4 py-3 text-muted-foreground">
                        <time
                          dateTime={doc.updated_at}
                          title={new Date(doc.updated_at).toLocaleString()}
                        >
                          {formatRelativeDate(doc.updated_at)}
                        </time>
                      </td>

                      {/* Actions */}
                      <td className="px-4 py-3">
                        {doc.status === "hitl_review" ? (
                          <Link
                            href={`/review/${doc.id}`}
                            aria-label={`Review document ${doc.filename}`}
                            className={cn(
                              "inline-flex items-center rounded-md px-3 py-1.5 text-xs font-medium",
                              "bg-primary text-primary-foreground",
                              "hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2",
                              "transition-colors",
                            )}
                          >
                            Review
                          </Link>
                        ) : (
                          <Link
                            href={`/documents/${doc.id}`}
                            aria-label={`View document ${doc.filename}`}
                            className={cn(
                              "inline-flex items-center rounded-md px-3 py-1.5 text-xs font-medium",
                              "border border-border text-muted-foreground",
                              "hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                              "transition-colors",
                            )}
                          >
                            View
                          </Link>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {(hasMore || page > 0) && (
            <div className="flex items-center justify-between px-1">
              <p className="text-xs text-muted-foreground">
                Showing {sortedDocuments.length} document{sortedDocuments.length !== 1 ? "s" : ""}
              </p>
              <div className="flex gap-2">
                {page > 0 && (
                  <button
                    type="button"
                    onClick={() => {
                      setPage(0);
                      void load(0);
                    }}
                    aria-label="Load first page of documents"
                    className={cn(
                      "rounded-md border border-border px-3 py-1.5 text-xs font-medium",
                      "hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                      "transition-colors",
                    )}
                  >
                    ← Back to start
                  </button>
                )}
                {hasMore && (
                  <button
                    type="button"
                    onClick={() => {
                      const nextPage = page + 1;
                      setPage(nextPage);
                      void load(nextPage);
                    }}
                    disabled={isLoading}
                    aria-label="Load more documents"
                    className={cn(
                      "rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground",
                      "hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2",
                      "disabled:cursor-not-allowed disabled:opacity-50 transition-colors",
                    )}
                  >
                    {isLoading ? "Loading…" : "Load more"}
                  </button>
                )}
              </div>
            </div>
          )}
        </>
      )}

      {loadError && documents.length > 0 && (
        <p role="alert" className="text-xs text-destructive px-1">
          {loadError}
        </p>
      )}
    </div>
  );
}

/** ------------------------------------------------------------------ *
 *  Sortable column header                                              *
 * ------------------------------------------------------------------- */

function SortableHeader({
  label,
  sortKey,
  currentKey,
  currentDir,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey;
  currentDir: SortDir;
  onSort: (key: SortKey) => void;
}) {
  const isActive = sortKey === currentKey;
  const ariaSortValue = isActive
    ? currentDir === "asc"
      ? "ascending"
      : "descending"
    : "none";

  return (
    <th
      scope="col"
      aria-sort={ariaSortValue}
      className="px-4 py-3 text-left font-semibold text-foreground"
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        aria-label={`Sort by ${label}${isActive ? `, currently ${ariaSortValue}` : ""}`}
        className={cn(
          "inline-flex items-center gap-1.5 rounded px-1 py-0.5 text-sm font-semibold",
          "hover:text-primary focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
          "transition-colors",
          isActive && "text-primary",
        )}
      >
        {label}
        <SortIcon direction={isActive ? currentDir : null} />
      </button>
    </th>
  );
}

function SortIcon({ direction }: { direction: SortDir | null }) {
  return (
    <svg
      aria-hidden="true"
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn(
        "transition-transform",
        direction === "desc" && "rotate-180",
        !direction && "opacity-30",
      )}
    >
      <polyline points="18 15 12 9 6 15" />
    </svg>
  );
}

/** ------------------------------------------------------------------ *
 *  Utility: relative date formatting                                   *
 * ------------------------------------------------------------------- */

function formatRelativeDate(isoString: string): string {
  const date = new Date(isoString);
  const now = Date.now();
  const diffMs = now - date.getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  const diffHrs = Math.floor(diffMs / 3_600_000);
  const diffDays = Math.floor(diffMs / 86_400_000);

  if (diffMin < 1) return "Just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHrs < 24) return `${diffHrs}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
