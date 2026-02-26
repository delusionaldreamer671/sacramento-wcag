/**
 * Dashboard home page — Server Component.
 * Displays pipeline stats and the document queue.
 */

import type { DocumentQueueStats, DocumentStatus, PDFDocument } from "@/lib/types";
import { DocumentQueue } from "@/components/document-queue";

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: string }> = {
  hitl_review: {
    label: "Needs Review",
    color: "bg-amber-50 text-amber-800 border-amber-200 ring-amber-100",
    icon: "!",
  },
  queued: {
    label: "Queued",
    color: "bg-sac-light text-sac-navy border-sac-blue/30 ring-sac-blue/10",
    icon: "\u2022",
  },
  complete: {
    label: "Complete",
    color: "bg-emerald-50 text-emerald-800 border-emerald-200 ring-emerald-100",
    icon: "\u2713",
  },
  failed: {
    label: "Failed",
    color: "bg-red-50 text-red-800 border-red-200 ring-red-100",
    icon: "\u2717",
  },
};

async function getInitialDocuments(): Promise<PDFDocument[]> {
  const baseUrl =
    process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ??
    "http://localhost:8000";

  try {
    const response = await fetch(`${baseUrl}/api/v1/documents?skip=0&limit=20`, {
      headers: { Accept: "application/json" },
      next: { revalidate: 30 },
    });

    if (!response.ok) return [];
    const raw = (await response.json()) as Array<Record<string, unknown>>;
    // Normalise: backend returns document_id; frontend expects id
    return raw.map((item) => ({
      id: (item.document_id ?? item.id) as string,
      filename: item.filename as string,
      gcs_input_path: (item.gcs_input_path ?? "") as string,
      gcs_output_path: (item.gcs_output_path ?? null) as string | null,
      status: item.status as DocumentStatus,
      page_count: (item.page_count ?? 0) as number,
      created_at: item.created_at as string,
      updated_at: item.updated_at as string,
    }));
  } catch {
    return [];
  }
}

function deriveStats(documents: PDFDocument[]): DocumentQueueStats {
  const byStatus = documents.reduce(
    (acc, doc) => {
      acc[doc.status] = (acc[doc.status] ?? 0) + 1;
      return acc;
    },
    {} as Record<DocumentStatus, number>,
  );

  return {
    total: documents.length,
    by_status: byStatus as Record<DocumentStatus, number>,
  };
}

export default async function DashboardPage() {
  const initialDocuments = await getInitialDocuments();
  const stats = deriveStats(initialDocuments);

  return (
    <div className="container mx-auto max-w-screen-xl px-4 py-8 sm:px-6 space-y-8">
      {/* Hero section */}
      <div className="rounded-xl bg-gradient-to-r from-sac-navy to-sac-dark p-8 text-white shadow-sac-md">
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
          WCAG Document Remediation
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-sac-blue/90 sm:text-base">
          Review and approve AI-generated accessibility remediation for
          Sacramento County PDF documents.
        </p>
        <div className="mt-4 flex items-center gap-3">
          <span className="inline-flex items-center gap-1 rounded-full bg-sac-gold/20 px-3 py-1 text-xs font-semibold text-sac-gold">
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-sac-gold" />
            WCAG 2.1 AA
          </span>
          <span className="inline-flex items-center gap-1 rounded-full bg-white/10 px-3 py-1 text-xs font-medium text-white/80">
            PDF/UA Compliant
          </span>
        </div>
      </div>

      {/* Stats grid */}
      <section aria-label="Pipeline status summary">
        <h2 className="mb-4 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
          Pipeline Overview
        </h2>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          {/* Total card — special treatment */}
          <div className="rounded-lg border border-sac-navy/10 bg-white p-5 shadow-sac ring-1 ring-sac-navy/5">
            <dt className="text-xs font-medium text-muted-foreground">Total Documents</dt>
            <dd className="mt-2 text-3xl font-bold tabular-nums text-sac-navy">{stats.total}</dd>
          </div>

          {Object.entries(STATUS_CONFIG).map(([status, config]) => (
            <div
              key={status}
              className={`rounded-lg border p-5 shadow-sac ring-1 ${config.color}`}
            >
              <dt className="flex items-center gap-2 text-xs font-medium">
                <span
                  aria-hidden="true"
                  className="flex h-5 w-5 items-center justify-center rounded-full bg-current/10 text-[10px] font-bold"
                >
                  {config.icon}
                </span>
                {config.label}
              </dt>
              <dd className="mt-2 text-3xl font-bold tabular-nums">
                {stats.by_status[status as DocumentStatus] ?? 0}
              </dd>
            </div>
          ))}
        </div>
      </section>

      {/* Document queue */}
      <section aria-labelledby="queue-heading">
        <div className="mb-4 flex items-center justify-between">
          <h2
            id="queue-heading"
            className="text-lg font-semibold text-foreground"
          >
            Document Queue
          </h2>
          <span className="rounded-md bg-secondary px-2.5 py-1 text-xs font-medium text-secondary-foreground">
            Documents requiring review are highlighted
          </span>
        </div>
        <div className="rounded-lg border border-border bg-card shadow-sac">
          <DocumentQueue initialDocuments={initialDocuments} />
        </div>
      </section>
    </div>
  );
}
