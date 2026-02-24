"use client";

import type { HITLReviewItem } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ElementViewerProps {
  /** The review item whose original_content should be rendered. */
  item: HITLReviewItem;
  /** Additional class names for the wrapper element. */
  className?: string;
}

/** ------------------------------------------------------------------ *
 *  Helpers — extract typed data from original_content                  *
 * ------------------------------------------------------------------- */

function getString(
  obj: Record<string, unknown>,
  key: string,
  fallback = "",
): string {
  const val = obj[key];
  return typeof val === "string" ? val : fallback;
}

function getNumber(
  obj: Record<string, unknown>,
  key: string,
  fallback = 0,
): number {
  const val = obj[key];
  return typeof val === "number" ? val : fallback;
}

function getArray<T>(obj: Record<string, unknown>, key: string): T[] {
  const val = obj[key];
  return Array.isArray(val) ? (val as T[]) : [];
}

/** ------------------------------------------------------------------ *
 *  Sub-renderers per element type                                       *
 * ------------------------------------------------------------------- */

function ImageElement({ content }: { content: Record<string, unknown> }) {
  const width = getNumber(content, "width", 0);
  const height = getNumber(content, "height", 0);
  const page = getNumber(content, "page", 1);
  const surroundingText = getString(content, "surrounding_text", "");
  const currentAlt = getString(content, "current_alt", "");

  return (
    <section aria-label="Original image element">
      {/* Bounding box placeholder — actual image render requires binary extraction */}
      <div
        role="img"
        aria-label={
          currentAlt
            ? `Image with existing alt text: "${currentAlt}"`
            : "Image with no alt text"
        }
        className={cn(
          "flex items-center justify-center rounded-md border-2 border-dashed",
          "bg-muted text-muted-foreground",
          "min-h-[120px]",
        )}
        style={{
          aspectRatio: width && height ? `${width}/${height}` : "16/9",
        }}
      >
        <div className="flex flex-col items-center gap-1 p-4 text-center">
          <svg
            aria-hidden="true"
            className="h-8 w-8 opacity-40"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909M3 21h18M3.75 3h16.5A.75.75 0 0121 3.75v16.5a.75.75 0 01-.75.75H3.75A.75.75 0 013 20.25V3.75A.75.75 0 013.75 3z"
            />
          </svg>
          <span className="text-sm font-medium">Image (binary not shown)</span>
          {width > 0 && height > 0 && (
            <span className="text-xs">
              {width} × {height} px
            </span>
          )}
          {page > 0 && (
            <span className="text-xs text-muted-foreground">Page {page}</span>
          )}
        </div>
      </div>

      <dl className="mt-3 space-y-1 text-sm">
        {currentAlt && (
          <div className="flex gap-2">
            <dt className="shrink-0 font-medium text-foreground">
              Existing alt:
            </dt>
            <dd className="text-muted-foreground italic">"{currentAlt}"</dd>
          </div>
        )}
        {!currentAlt && (
          <div className="flex gap-2">
            <dt className="shrink-0 font-medium text-destructive">
              Existing alt:
            </dt>
            <dd className="text-destructive">None (WCAG 1.1.1 violation)</dd>
          </div>
        )}
        {surroundingText && (
          <div>
            <dt className="font-medium text-foreground">Surrounding text:</dt>
            <dd className="mt-0.5 rounded bg-muted px-2 py-1 text-xs text-muted-foreground">
              …{surroundingText}…
            </dd>
          </div>
        )}
      </dl>
    </section>
  );
}

function TableElement({ content }: { content: Record<string, unknown> }) {
  type CellData = { text: string; is_header?: boolean; colspan?: number; rowspan?: number };
  type RowData = CellData[];
  const rows = getArray<RowData>(content, "rows");
  const caption = getString(content, "caption", "");
  const nestingDepth = getNumber(content, "nesting_depth", 1);

  if (rows.length === 0) {
    return (
      <p className="text-sm text-muted-foreground italic">
        Table data could not be extracted.
      </p>
    );
  }

  return (
    <section aria-label="Original table element">
      {nestingDepth > 1 && (
        <p className="mb-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-700" role="alert">
          Nested table — depth {nestingDepth}. Manual review recommended.
        </p>
      )}
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full text-sm" aria-label={caption || "Extracted table"}>
          {caption && (
            <caption className="bg-muted px-3 py-1.5 text-left text-xs font-medium text-muted-foreground">
              {caption}
            </caption>
          )}
          <tbody>
            {rows.map((row, rowIdx) => (
              <tr
                key={rowIdx}
                className="border-b border-border last:border-0 odd:bg-muted/30"
              >
                {row.map((cell, cellIdx) => {
                  const CellTag = cell.is_header ? "th" : "td";
                  return (
                    <CellTag
                      key={cellIdx}
                      colSpan={cell.colspan ?? 1}
                      rowSpan={cell.rowspan ?? 1}
                      scope={cell.is_header ? "col" : undefined}
                      className={cn(
                        "px-3 py-2 text-left align-top",
                        cell.is_header
                          ? "font-semibold text-foreground"
                          : "text-muted-foreground",
                      )}
                    >
                      {cell.text || <span className="text-xs italic opacity-50">(empty)</span>}
                    </CellTag>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground">
        {rows.length} row{rows.length !== 1 ? "s" : ""}
        {rows[0] ? ` × ${rows[0].length} column${rows[0].length !== 1 ? "s" : ""}` : ""}
      </p>
    </section>
  );
}

function HeadingElement({ content }: { content: Record<string, unknown> }) {
  const text = getString(content, "text", "(no text extracted)");
  const level = getNumber(content, "level", 2) as 1 | 2 | 3 | 4 | 5 | 6;
  const currentTag = getString(content, "current_tag", "");

  const validLevel = Math.min(Math.max(level, 1), 6) as 1 | 2 | 3 | 4 | 5 | 6;
  const HeadingTag = `h${validLevel}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";

  const sizeMap: Record<number, string> = {
    1: "text-2xl",
    2: "text-xl",
    3: "text-lg",
    4: "text-base",
    5: "text-sm",
    6: "text-xs",
  };

  return (
    <section aria-label="Original heading element">
      <div className="rounded-md border border-border bg-muted/40 p-4">
        <HeadingTag
          className={cn(
            "font-bold text-foreground",
            sizeMap[validLevel] ?? "text-base",
          )}
        >
          {text}
        </HeadingTag>
      </div>
      <dl className="mt-2 flex gap-4 text-sm">
        <div className="flex gap-1.5">
          <dt className="font-medium text-foreground">Detected level:</dt>
          <dd>
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
              H{validLevel}
            </code>
          </dd>
        </div>
        {currentTag && (
          <div className="flex gap-1.5">
            <dt className="font-medium text-foreground">PDF tag:</dt>
            <dd>
              <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                {currentTag}
              </code>
            </dd>
          </div>
        )}
      </dl>
    </section>
  );
}

function LinkElement({ content }: { content: Record<string, unknown> }) {
  const linkText = getString(content, "text", "(no link text)");
  const destination = getString(content, "destination", "");
  const isExternal =
    typeof content.is_external === "boolean" ? content.is_external : false;
  const context = getString(content, "context_sentence", "");

  const hasDescriptiveText =
    linkText.toLowerCase() !== "click here" &&
    linkText.toLowerCase() !== "here" &&
    linkText.toLowerCase() !== "read more" &&
    linkText !== destination;

  return (
    <section aria-label="Original link element">
      <div className="rounded-md border border-border bg-muted/40 p-4">
        <p className="text-sm text-muted-foreground">Link text:</p>
        <p
          className={cn(
            "mt-1 font-medium",
            hasDescriptiveText ? "text-foreground" : "text-destructive",
          )}
          aria-label={`Link text: ${linkText}`}
        >
          "{linkText}"
        </p>
        {!hasDescriptiveText && (
          <p className="mt-1 text-xs text-destructive" role="alert">
            Non-descriptive link text (WCAG 2.4.4 violation)
          </p>
        )}
      </div>

      <dl className="mt-2 space-y-1 text-sm">
        {destination && (
          <div>
            <dt className="font-medium text-foreground">Destination:</dt>
            <dd className="mt-0.5 break-all rounded bg-muted px-2 py-1 text-xs font-mono text-muted-foreground">
              {destination}
              {isExternal && (
                <span className="ml-2 text-amber-600">(external)</span>
              )}
            </dd>
          </div>
        )}
        {context && (
          <div>
            <dt className="font-medium text-foreground">Context:</dt>
            <dd className="mt-0.5 text-xs text-muted-foreground italic">
              …{context}…
            </dd>
          </div>
        )}
      </dl>
    </section>
  );
}

function GenericElement({ content, elementType }: { content: Record<string, unknown>; elementType: string }) {
  const text = getString(content, "text", "");
  const tag = getString(content, "tag", "");

  return (
    <section aria-label={`Original ${elementType} element`}>
      <div className="rounded-md border border-border bg-muted/40 p-4">
        {tag && (
          <p className="mb-1 text-xs text-muted-foreground">
            Tag:{" "}
            <code className="rounded bg-muted px-1 py-0.5">{tag}</code>
          </p>
        )}
        {text ? (
          <p className="text-sm text-foreground">{text}</p>
        ) : (
          <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-muted-foreground">
            {JSON.stringify(content, null, 2)}
          </pre>
        )}
      </div>
    </section>
  );
}

/** ------------------------------------------------------------------ *
 *  Main component                                                       *
 * ------------------------------------------------------------------- */

export function ElementViewer({ item, className }: ElementViewerProps) {
  const { element_type, original_content } = item;

  return (
    <div className={cn("text-sm", className)}>
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Original Element
      </p>

      {element_type === "image" || element_type === "figure" ? (
        <ImageElement content={original_content} />
      ) : element_type === "table" ? (
        <TableElement content={original_content} />
      ) : element_type === "heading" ? (
        <HeadingElement content={original_content} />
      ) : element_type === "link" ? (
        <LinkElement content={original_content} />
      ) : (
        <GenericElement content={original_content} elementType={element_type} />
      )}
    </div>
  );
}
