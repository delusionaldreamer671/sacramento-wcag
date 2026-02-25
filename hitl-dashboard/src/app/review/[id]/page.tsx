/**
 * Document review page — Server Component wrapper.
 * Route: /review/[id]
 * Displays the ReviewPanel for a specific document.
 */

import type { Metadata } from "next";
import Link from "next/link";
import { ReviewPanel } from "@/components/review-panel";
import { ChangeProposalForm } from "@/components/change-proposal-form";
import { cn } from "@/lib/utils";

interface ReviewPageProps {
  params: { id: string };
  searchParams?: { item?: string };
}

export async function generateMetadata({
  params,
}: ReviewPageProps): Promise<Metadata> {
  return {
    title: `Review Document ${params.id.slice(0, 8)} — WCAG Remediation Dashboard`,
  };
}

export default function ReviewPage({ params, searchParams }: ReviewPageProps) {
  const documentId = params.id;
  const initialItemId = searchParams?.item;

  return (
    <div className="container mx-auto max-w-screen-xl px-4 py-6 space-y-6">
      {/* Breadcrumb navigation */}
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
            Review Document
          </li>
        </ol>
      </nav>

      {/* Page heading */}
      <header>
        <h1 className="text-xl font-bold tracking-tight text-foreground">
          Document Review
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Review AI-generated remediation suggestions. Use{" "}
          <kbd className="kbd">←</kbd> / <kbd className="kbd">→</kbd> to
          navigate items, or{" "}
          <kbd className="kbd">Alt+A</kbd>{" "}
          <kbd className="kbd">Alt+E</kbd>{" "}
          <kbd className="kbd">Alt+R</kbd>{" "}
          for quick decisions.
        </p>
      </header>

      {/* Review panel */}
      <ReviewPanel
        documentId={documentId}
        initialItemId={initialItemId}
        reviewerId="county-reviewer"
      />

      {/* Change proposal section */}
      <section aria-labelledby="change-proposal-heading">
        <details className="group rounded-lg border border-border bg-card">
          <summary
            className={cn(
              "flex cursor-pointer list-none items-center justify-between px-5 py-4",
              "text-sm font-medium text-foreground select-none",
              "hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2",
              "focus-visible:ring-ring focus-visible:ring-inset transition-colors",
            )}
            id="change-proposal-heading"
          >
            <span>Propose a Change to This Document</span>
            {/* Chevron rotates when open */}
            <svg
              className="h-4 w-4 text-muted-foreground transition-transform group-open:rotate-180"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 20 20"
              fill="currentColor"
              aria-hidden="true"
            >
              <path
                fillRule="evenodd"
                d="M5.22 8.22a.75.75 0 0 1 1.06 0L10 11.94l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 9.28a.75.75 0 0 1 0-1.06Z"
                clipRule="evenodd"
              />
            </svg>
          </summary>
          <div className="border-t border-border px-5 py-4">
            <p className="mb-4 text-xs text-muted-foreground">
              Use this form to suggest a change to the remediation approach for this
              document. Your proposal will be evaluated against WCAG 2.1 AA compliance
              requirements before being applied.
            </p>
            <ChangeProposalForm documentId={documentId} />
          </div>
        </details>
      </section>
    </div>
  );
}
