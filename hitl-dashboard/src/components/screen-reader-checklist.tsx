"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

/** A single checklist item definition. */
interface CheckItem {
  id: string;
  label: string;
}

/** A named group of related checklist items. */
interface CheckGroup {
  id: string;
  heading: string;
  items: CheckItem[];
}

const CHECK_GROUPS: CheckGroup[] = [
  {
    id: "screen-reader",
    heading: "Screen Reader Testing (NVDA / JAWS / VoiceOver)",
    items: [
      {
        id: "sr-reading-order",
        label:
          "Reading order: Does the content flow logically when read aloud?",
      },
      {
        id: "sr-headings",
        label:
          "Headings: Are heading levels announced correctly (H1, H2, H3)?",
      },
      {
        id: "sr-tables",
        label:
          "Tables: Are column and row headers announced when navigating cells?",
      },
      {
        id: "sr-images",
        label: "Images: Is alt text read aloud and descriptive?",
      },
      {
        id: "sr-lists",
        label: 'Lists: Are list items announced as "list, N items"?',
      },
    ],
  },
  {
    id: "keyboard",
    heading: "Keyboard Navigation",
    items: [
      {
        id: "kb-tab-reach",
        label: "Can all interactive elements be reached with Tab?",
      },
      {
        id: "kb-focus-visible",
        label: "Is focus visible on all elements?",
      },
      {
        id: "kb-forms",
        label: "Can forms be completed without a mouse?",
      },
    ],
  },
  {
    id: "visual",
    heading: "Visual Verification",
    items: [
      {
        id: "vis-contrast",
        label: "Is color contrast sufficient (4.5:1 for text)?",
      },
      {
        id: "vis-color-only",
        label:
          "Is information conveyed without relying on color alone?",
      },
      {
        id: "vis-reflow",
        label: "Does content reflow at 200% zoom?",
      },
    ],
  },
];

const ALL_ITEM_IDS: string[] = CHECK_GROUPS.flatMap((g) =>
  g.items.map((item) => item.id),
);
const TOTAL = ALL_ITEM_IDS.length;

/** Individual checkbox row. */
function CheckRow({
  item,
  checked,
  onChange,
}: {
  item: CheckItem;
  checked: boolean;
  onChange: (id: string, value: boolean) => void;
}) {
  return (
    <li className="flex items-start gap-3">
      <input
        type="checkbox"
        id={item.id}
        checked={checked}
        onChange={(e) => onChange(item.id, e.target.checked)}
        className={cn(
          "mt-0.5 h-4 w-4 flex-shrink-0 rounded border-border text-primary",
          "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          "cursor-pointer",
        )}
        aria-checked={checked}
      />
      <label
        htmlFor={item.id}
        className={cn(
          "cursor-pointer select-none text-sm leading-snug",
          checked ? "text-muted-foreground line-through" : "text-foreground",
        )}
      >
        {item.label}
      </label>
    </li>
  );
}

/** Collapsible manual accessibility verification checklist for HITL reviewers. */
export function ScreenReaderChecklist({ className }: { className?: string }) {
  const [checked, setChecked] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(ALL_ITEM_IDS.map((id) => [id, false])),
  );

  const completedCount = Object.values(checked).filter(Boolean).length;

  function handleChange(id: string, value: boolean) {
    setChecked((prev) => ({ ...prev, [id]: value }));
  }

  const progressPercent = TOTAL > 0 ? Math.round((completedCount / TOTAL) * 100) : 0;

  return (
    <details
      className={cn(
        "group rounded-lg border border-border bg-card",
        className,
      )}
    >
      {/* Summary acts as the toggle button — native keyboard accessible */}
      <summary
        className={cn(
          "flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3",
          "rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          "hover:bg-muted/40 transition-colors",
        )}
        aria-label="Manual Accessibility Verification Checklist — click to expand or collapse"
      >
        <div className="flex items-center gap-3">
          {/* Expand/collapse indicator */}
          <ChevronIcon
            className={cn(
              "h-4 w-4 flex-shrink-0 text-muted-foreground transition-transform duration-200",
              "group-open:rotate-90",
            )}
            aria-hidden="true"
          />
          <span className="text-sm font-semibold text-foreground">
            Manual Accessibility Verification Checklist
          </span>
        </div>

        {/* Progress counter — always visible even when collapsed */}
        <span
          className={cn(
            "flex-shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium tabular-nums",
            completedCount === TOTAL
              ? "bg-green-100 text-green-800"
              : "bg-muted text-muted-foreground",
          )}
          aria-label={`${completedCount} of ${TOTAL} checks completed`}
        >
          {completedCount} / {TOTAL}
        </span>
      </summary>

      {/* Checklist body */}
      <div className="border-t border-border px-4 pb-4 pt-3">
        {/* Thin progress bar */}
        <div
          role="progressbar"
          aria-valuenow={completedCount}
          aria-valuemin={0}
          aria-valuemax={TOTAL}
          aria-label={`${completedCount} of ${TOTAL} checks completed (${progressPercent}%)`}
          className="mb-4 h-1.5 overflow-hidden rounded-full bg-muted"
        >
          <div
            className={cn(
              "h-full rounded-full transition-all duration-300",
              completedCount === TOTAL ? "bg-green-500" : "bg-primary",
            )}
            style={{ width: `${progressPercent}%` }}
          />
        </div>

        <p className="mb-4 text-xs text-muted-foreground">
          Use these checks to verify the remediated document before approving.
          Progress is not saved — this is a session-only aid for the current review.
        </p>

        <div className="space-y-5">
          {CHECK_GROUPS.map((group) => (
            <fieldset key={group.id} className="space-y-2.5">
              <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {group.heading}
              </legend>
              <ul
                role="list"
                aria-label={group.heading}
                className="space-y-2.5 pl-1"
              >
                {group.items.map((item) => (
                  <CheckRow
                    key={item.id}
                    item={item}
                    checked={checked[item.id] ?? false}
                    onChange={handleChange}
                  />
                ))}
              </ul>
            </fieldset>
          ))}
        </div>

        {completedCount === TOTAL && TOTAL > 0 && (
          <p
            role="status"
            aria-live="polite"
            className="mt-4 rounded-md bg-green-50 border border-green-200 px-3 py-2 text-sm font-medium text-green-800"
          >
            All {TOTAL} checks completed. The document is ready for final approval.
          </p>
        )}
      </div>
    </details>
  );
}

/** Inline chevron icon — avoids extra import overhead. */
function ChevronIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}
