/**
 * Document status/detail page.
 * Route: /documents/[id]
 * Shows document metadata and links to review if in hitl_review state.
 */

import type { Metadata } from "next";
import Link from "next/link";
import { cn } from "@/lib/utils";
import type { DocumentStatus } from "@/lib/types";

interface DocumentPageProps {
  params: { id: string };
}

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

export async function generateMetadata({
  params,
}: DocumentPageProps): Promise<Metadata> {
  return {
    title: `Document ${params.id.slice(0, 8)} — WCAG Remediation Dashboard`,
  };
}

async function getDocumentStatus(id: string) {
  const baseUrl =
    process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ??
    "http://localhost:8000";

  try {
    const res = await fetch(
      `${baseUrl}/api/documents/${encodeURIComponent(id)}`,
      {
        headers: { Accept: "application/json" },
        next: { revalidate: 15 },
      },
    );
    if (!res.ok) return null;
    return (await res.json()) as {
      document_id: string;
      filename: string;
      status: DocumentStatus;
      page_count: number;
      created_at: string;
      updated_at: string;
    };
  } catch {
    return null;
  }
}

export default async function DocumentPage({ params }: DocumentPageProps) {
  const doc = await getDocumentStatus(params.id);

  return (
    <div className="container mx-auto max-w-screen-xl px-4 py-6 space-y-6">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb">
        <ol className="flex items-center gap-2 text-sm text-muted-foreground">
          <li>
            <Link
              href="/"
              className={cn(
                "hover:text-foreground focus-visible:rounded focus-visible:ring-2",
                "focus-visible:ring-ring focus-visible:ring-offset-2 transition-colors",
              )}
            >
              Dashboard
            </Link>
          </li>
          <li aria-hidden="true">
            <span>/</span>
          </li>
          <li aria-current="page" className="text-foreground font-medium">
            Document
          </li>
        </ol>
      </nav>

      {doc ? (
        <article aria-labelledby="doc-heading">
          <div className="rounded-lg border border-border bg-card p-6 space-y-4">
            <header className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h1
                  id="doc-heading"
                  className="text-lg font-bold text-foreground break-all"
                >
                  {doc.filename}
                </h1>
                <p className="mt-0.5 text-xs font-mono text-muted-foreground">
                  ID: {doc.document_id}
                </p>
              </div>
              <span
                className={cn(
                  "status-badge border self-start shrink-0",
                  STATUS_COLORS[doc.status],
                )}
              >
                {STATUS_LABELS[doc.status]}
              </span>
            </header>

            <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3">
              <div>
                <dt className="font-medium text-muted-foreground">Pages</dt>
                <dd className="mt-0.5 text-foreground">
                  {doc.page_count > 0 ? doc.page_count : "Unknown"}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-muted-foreground">Created</dt>
                <dd className="mt-0.5 text-foreground">
                  <time dateTime={doc.created_at}>
                    {new Date(doc.created_at).toLocaleString()}
                  </time>
                </dd>
              </div>
              <div>
                <dt className="font-medium text-muted-foreground">Updated</dt>
                <dd className="mt-0.5 text-foreground">
                  <time dateTime={doc.updated_at}>
                    {new Date(doc.updated_at).toLocaleString()}
                  </time>
                </dd>
              </div>
            </dl>

            {(doc.status === "hitl_review" || doc.status === "approved") && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
                <p className="text-sm font-medium text-amber-800">
                  Human review required
                </p>
                <p className="mt-1 text-xs text-amber-700">
                  This document requires at least one human review action before
                  it can be marked as complete. Automated validation alone is not
                  sufficient for WCAG 2.1 AA compliance.
                </p>
              </div>
            )}

            {doc.status === "hitl_review" && (
              <div className="pt-2">
                <Link
                  href={`/review/${doc.document_id}`}
                  className={cn(
                    "inline-flex items-center rounded-md px-4 py-2 text-sm font-medium",
                    "bg-primary text-primary-foreground",
                    "hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2",
                    "transition-colors",
                  )}
                >
                  Start Review
                </Link>
              </div>
            )}
          </div>
        </article>
      ) : (
        <div
          role="alert"
          className="rounded-lg border border-border bg-muted/30 p-8 text-center"
        >
          <h1 className="text-base font-semibold text-foreground">
            Document not found
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            The document{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
              {params.id}
            </code>{" "}
            could not be loaded. It may not exist or the API may be unavailable.
          </p>
          <Link
            href="/"
            className={cn(
              "mt-4 inline-flex items-center rounded-md border border-border px-4 py-2 text-sm",
              "hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              "transition-colors",
            )}
          >
            ← Back to Dashboard
          </Link>
        </div>
      )}
    </div>
  );
}
