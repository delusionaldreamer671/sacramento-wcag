/**
 * Document review page — Server Component wrapper.
 * Route: /review/[id]
 * Displays the ReviewPanel for a specific document.
 */

import type { Metadata } from "next";
import Link from "next/link";
import { ReviewPanel } from "@/components/review-panel";
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
    </div>
  );
}
