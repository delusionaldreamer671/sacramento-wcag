"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { fetchAuditTrail } from "@/lib/api";
import type { AuditEntry } from "@/lib/types";

interface AuditTrailProps {
  entityType: string;
  entityId: string;
  className?: string;
}

export function AuditTrail({ entityType, entityId, className }: AuditTrailProps) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchAuditTrail(entityType, entityId)
      .then(setEntries)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [entityType, entityId]);

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading audit trail...</p>;
  }

  if (entries.length === 0) {
    return (
      <p className={cn("text-sm text-muted-foreground", className)}>
        No audit history for this item.
      </p>
    );
  }

  return (
    <div className={cn("space-y-2", className)}>
      <h4 className="text-sm font-semibold text-foreground">Audit Trail</h4>
      <ol className="relative border-l border-border ml-2 space-y-3" aria-label="Audit history">
        {entries.map((entry) => (
          <li key={entry.id} className="ml-4">
            <div className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border border-border bg-background" />
            <time className="text-xs text-muted-foreground">
              {new Date(entry.timestamp).toLocaleString()}
            </time>
            <p className="text-sm text-foreground">
              <span className="font-medium">{entry.action}</span>
              {entry.performed_by && (
                <span className="text-muted-foreground"> by {entry.performed_by}</span>
              )}
            </p>
            {(entry.old_value || entry.new_value) && (
              <div className="mt-1 text-xs text-muted-foreground">
                {entry.old_value && <span>From: {entry.old_value}</span>}
                {entry.old_value && entry.new_value && <span> → </span>}
                {entry.new_value && <span>To: {entry.new_value}</span>}
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
