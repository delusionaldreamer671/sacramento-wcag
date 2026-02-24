"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { fetchRules, updateRuleStatus, createRule } from "@/lib/api";
import type { Rule, RuleStatus } from "@/lib/types";

export default function RulesPage() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<RuleStatus | "all">("all");
  const [showCreate, setShowCreate] = useState(false);

  const loadRules = () => {
    setLoading(true);
    const statusParam = filter === "all" ? undefined : filter;
    fetchRules(statusParam)
      .then(setRules)
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadRules(); }, [filter]);

  const handleStatusChange = async (ruleId: string, newStatus: string) => {
    try {
      await updateRuleStatus(ruleId, newStatus);
      loadRules();
    } catch (err) {
      console.error("Failed to update rule status:", err);
    }
  };

  return (
    <div className="container mx-auto max-w-screen-xl px-4 py-6 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-foreground">Rules Ledger</h1>
          <p className="text-sm text-muted-foreground">
            Manage remediation rules. Admin access required.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className={cn(
            "rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground",
            "hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          {showCreate ? "Cancel" : "New Rule"}
        </button>
      </header>

      {/* Filter tabs */}
      <nav aria-label="Filter rules by status" className="flex gap-2">
        {(["all", "active", "candidate", "retired"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm capitalize",
              filter === s
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:bg-muted/80",
            )}
            aria-current={filter === s ? "page" : undefined}
          >
            {s}
          </button>
        ))}
      </nav>

      {showCreate && <CreateRuleForm onCreated={() => { setShowCreate(false); loadRules(); }} />}

      {loading ? (
        <p className="text-sm text-muted-foreground py-8 text-center">Loading rules...</p>
      ) : rules.length === 0 ? (
        <div className="rounded-lg border border-border bg-muted/30 p-8 text-center">
          <p className="font-medium">No rules found</p>
          <p className="text-sm text-muted-foreground mt-1">
            {filter === "all" ? "Create a new rule to get started." : `No ${filter} rules.`}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {rules.map((rule) => (
            <article
              key={rule.id}
              className="rounded-lg border border-border bg-card p-4 space-y-2"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="font-mono text-sm font-semibold text-foreground">
                    {rule.trigger_pattern}
                  </p>
                  <p className="text-xs text-muted-foreground font-mono">
                    ID: {rule.id.slice(0, 8)}... | v{rule.version} | confidence: {rule.confidence.toFixed(2)}
                  </p>
                </div>
                <RuleStatusBadge status={rule.status} />
              </div>
              <div className="text-xs text-muted-foreground">
                Action: {JSON.stringify(rule.action)} | Validated on {rule.validated_on_docs?.length ?? 0} docs
              </div>
              <div className="flex gap-2 pt-1">
                {rule.status === "candidate" && (
                  <button
                    onClick={() => handleStatusChange(rule.id, "active")}
                    className="rounded-md bg-green-600 px-2 py-1 text-xs text-white hover:bg-green-700"
                  >
                    Promote to Active
                  </button>
                )}
                {rule.status === "active" && (
                  <button
                    onClick={() => handleStatusChange(rule.id, "retired")}
                    className="rounded-md bg-red-600 px-2 py-1 text-xs text-white hover:bg-red-700"
                  >
                    Retire
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

function RuleStatusBadge({ status }: { status: RuleStatus }) {
  const colors: Record<RuleStatus, string> = {
    active: "bg-green-100 text-green-800 border-green-200",
    candidate: "bg-amber-100 text-amber-800 border-amber-200",
    retired: "bg-slate-100 text-slate-700 border-slate-200",
  };
  return (
    <span className={cn("inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold capitalize", colors[status])}>
      {status}
    </span>
  );
}

function CreateRuleForm({ onCreated }: { onCreated: () => void }) {
  const [pattern, setPattern] = useState("");
  const [actionType, setActionType] = useState("add_scope");
  const [actionValue, setActionValue] = useState("");
  const [confidence, setConfidence] = useState("0.8");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await createRule({
        trigger_pattern: pattern,
        action: { type: actionType, value: actionValue },
        confidence: parseFloat(confidence),
      });
      onCreated();
    } catch (err) {
      console.error("Failed to create rule:", err);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="rounded-lg border border-border bg-card p-4 space-y-3">
      <h2 className="text-sm font-semibold text-foreground">Create New Rule</h2>
      <div className="grid grid-cols-2 gap-3">
        <label className="space-y-1">
          <span className="text-xs font-medium text-muted-foreground">Trigger Pattern</span>
          <input
            type="text"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            placeholder="table:missing_headers"
            required
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium text-muted-foreground">Confidence</span>
          <input
            type="number"
            value={confidence}
            onChange={(e) => setConfidence(e.target.value)}
            min="0" max="1" step="0.05"
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium text-muted-foreground">Action Type</span>
          <select
            value={actionType}
            onChange={(e) => setActionType(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
          >
            <option value="add_scope">add_scope</option>
            <option value="set_alt">set_alt</option>
            <option value="remove_element">remove_element</option>
            <option value="set_heading">set_heading</option>
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium text-muted-foreground">Action Value</span>
          <input
            type="text"
            value={actionValue}
            onChange={(e) => setActionValue(e.target.value)}
            placeholder="col"
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
          />
        </label>
      </div>
      <button
        type="submit"
        disabled={submitting || !pattern}
        className={cn(
          "rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground",
          "hover:bg-primary/90 disabled:opacity-50",
        )}
      >
        {submitting ? "Creating..." : "Create Rule"}
      </button>
    </form>
  );
}
