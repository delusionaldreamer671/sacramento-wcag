"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { fetchDocuments } from "@/lib/api";
import type { PDFDocument } from "@/lib/types";

export default function IssuesPage() {
  const [documents, setDocuments] = useState<PDFDocument[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchDocuments(0, 100)
      .then((docs) => {
        setDocuments(docs.filter((d) => d.status === "hitl_review"));
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  return (
    <div className="container mx-auto max-w-screen-xl px-4 py-8 sm:px-6 space-y-6">
      <header className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-100 text-amber-700">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        </div>
        <div>
          <h1 className="text-xl font-bold text-foreground">Issues Queue</h1>
          <p className="text-sm text-muted-foreground">
            Cross-document view of items needing human review
          </p>
        </div>
      </header>

      {loading ? (
        <div role="status" className="flex items-center justify-center rounded-lg border border-border bg-card py-16 shadow-sac">
          <div className="flex flex-col items-center gap-2">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-sac-navy/20 border-t-sac-navy" />
            <p className="text-sm text-muted-foreground">Loading issues...</p>
          </div>
        </div>
      ) : documents.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-12 text-center shadow-sac">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-emerald-50">
            <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-emerald-600" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
          </div>
          <p className="mt-4 font-semibold text-foreground">No pending issues</p>
          <p className="mt-1 text-sm text-muted-foreground">
            All documents have been reviewed or are still processing.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {documents.map((doc) => (
            <Link
              key={doc.id}
              href={`/documents/${doc.id}`}
              className={cn(
                "block rounded-lg border border-border bg-card p-5 shadow-sac",
                "hover:shadow-sac-md hover:border-sac-navy/20 transition-all",
                "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              )}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded bg-sac-light text-sac-navy">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                  </div>
                  <div>
                    <p className="font-medium text-foreground">{doc.filename}</p>
                    <p className="text-xs text-muted-foreground font-mono">
                      {doc.id.slice(0, 8)}...
                    </p>
                  </div>
                </div>
                <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800">
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-500" aria-hidden="true" />
                  Needs Review
                </span>
              </div>
              <div className="mt-3 flex gap-4 text-xs text-muted-foreground">
                <span>{doc.page_count} pages</span>
                <span>Updated {new Date(doc.updated_at).toLocaleDateString()}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
