"""Change proposal evaluation — deterministic scoring.

Evaluates human-submitted change proposals against the document's
validation state. Scoring is rule-based: no AI is involved in this module.

A change proposal is a reviewer's intent to modify an existing HITLReviewItem
(e.g. editing the AI-suggested alt text, overriding a table structure fix).

The evaluator answers three questions deterministically:
  1. Does this change improve or harm WCAG compliance? (compliance_impact)
  2. What is the risk of the change going wrong? (risk)
  3. Should the pipeline auto-approve or flag for additional sign-off? (recommendation)

Usage:

    from services.common.change_evaluator import evaluate_proposal, apply_proposal

    result = evaluate_proposal(
        human_comment="Change alt text to describe the chart in detail",
        element_type="image",
        finding_severity="critical",
        finding_criterion="1.1.1",
    )
    # result["recommendation"] -> "approve" | "reject"
    # result["risk"] -> "low" | "medium" | "high"
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword sets for scope detection
# ---------------------------------------------------------------------------

# Comment phrases that indicate the reviewer intends to apply this change
# globally (across all documents), not just to the current element.
_GLOBAL_SCOPE_KEYWORDS: frozenset[str] = frozenset(
    ["always", "all documents", "every", "whenever", "global", "all cases"]
)

# Phrase patterns for global scope matching (compiled once at import time).
_GLOBAL_SCOPE_RE = re.compile(
    r"\b(" + "|".join(re.escape(kw) for kw in _GLOBAL_SCOPE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Scoring logic helpers
# ---------------------------------------------------------------------------


def _compute_compliance_impact(
    human_comment: str,
    finding_severity: Optional[str],
    finding_criterion: Optional[str],
    reviewer_decision: Optional[str] = None,
) -> str:
    """Determine whether the proposal improves, harms, or is neutral to compliance.

    Rules (applied in order, first match wins):
      1. Negative: reviewer is modifying an already-approved item (decision=="approve"
         on an item that already has reviewer_decision=="approve").
      2. Positive: finding_severity is "critical" or "serious" AND a WCAG criterion
         is present AND the comment is non-empty (i.e. reviewer is actively addressing
         a high-severity, criterion-linked issue).
      3. Neutral: everything else.

    Args:
        human_comment:     Free-text comment from the reviewer.
        finding_severity:  "critical", "serious", "moderate", or "minor" (or None).
        finding_criterion: WCAG criterion ID like "1.1.1" (or None).
        reviewer_decision: If provided and "approve", treats this as an override of
                           an already-approved item (negative impact).

    Returns:
        "positive", "neutral", or "negative".
    """
    # Rule 1 — Overriding an already-approved item is negative
    if reviewer_decision == "approve":
        return "negative"

    # Rule 2 — High-severity finding with criterion and substantive comment
    high_severity = finding_severity in ("critical", "serious")
    has_criterion = bool(finding_criterion)
    has_comment = bool(human_comment and human_comment.strip())

    if high_severity and has_criterion and has_comment:
        return "positive"

    return "neutral"


def _compute_risk(human_comment: str, element_type: str) -> str:
    """Determine risk level for the proposed change.

    Rules (applied in order, first match wins):
      1. High: element_type is "table" (complex structural changes are high-risk).
      2. High: comment contains global-scope keywords ("always", "all documents",
               "every", "whenever") — global rules have broad blast radius.
      3. Low: element_type is "image" (alt text edits are isolated and easily reversed).
      4. Medium: everything else.

    Args:
        human_comment: Free-text comment from the reviewer.
        element_type:  "image", "table", "heading", "link", "paragraph", etc.

    Returns:
        "low", "medium", or "high".
    """
    # Rule 1 — Tables are structurally complex
    if element_type == "table":
        return "high"

    # Rule 2 — Global-scope keywords in comment
    if _GLOBAL_SCOPE_RE.search(human_comment):
        return "high"

    # Rule 3 — Image alt text edits are low-risk
    if element_type == "image":
        return "low"

    # Rule 4 — Everything else
    return "medium"


def _compute_scope(human_comment: str) -> str:
    """Determine whether the proposal affects only the current element or is global.

    Returns "global_rule" if the comment contains global-scope language,
    otherwise "single_doc".
    """
    if _GLOBAL_SCOPE_RE.search(human_comment):
        return "global_rule"
    return "single_doc"


def _build_evidence(
    element_type: str,
    finding_severity: Optional[str],
    finding_criterion: Optional[str],
    compliance_impact: str,
    risk: str,
    scope: str,
) -> str:
    """Construct a human-readable evidence string summarising the evaluation inputs."""
    parts: list[str] = [f"element_type={element_type}"]

    if finding_severity:
        parts.append(f"finding_severity={finding_severity}")
    if finding_criterion:
        parts.append(f"wcag_criterion={finding_criterion}")

    parts.append(f"compliance_impact={compliance_impact}")
    parts.append(f"risk={risk}")
    parts.append(f"scope={scope}")

    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_proposal(
    human_comment: str,
    element_type: str,
    finding_severity: Optional[str] = None,
    finding_criterion: Optional[str] = None,
    reviewer_decision: Optional[str] = None,
) -> dict[str, Any]:
    """Produce a system evaluation for a change proposal.

    All scoring is deterministic — no AI or external calls.

    Args:
        human_comment:     Free-text from the reviewer describing the intended change.
        element_type:      Type of the PDF element being changed ("image", "table",
                           "heading", "link", "paragraph", "form_field", etc.).
        finding_severity:  Severity of the associated WCAG finding, if any.
                           Expected values: "critical", "serious", "moderate", "minor".
        finding_criterion: WCAG 2.1 criterion ID (e.g. "1.1.1") if a finding exists.
        reviewer_decision: Current decision on the item ("approve", "edit", "reject").
                           Used to detect overrides of already-approved items.

    Returns:
        dict with keys:
            compliance_impact: "positive" | "neutral" | "negative"
            risk:              "low" | "medium" | "high"
            reversibility:     True (originals are always preserved in the IR)
            scope:             "single_doc" | "global_rule"
            evidence:          Human-readable summary of evaluation inputs
            recommendation:    "approve" | "reject"
            reason:            Plain-English justification for the recommendation
    """
    compliance_impact = _compute_compliance_impact(
        human_comment=human_comment,
        finding_severity=finding_severity,
        finding_criterion=finding_criterion,
        reviewer_decision=reviewer_decision,
    )
    risk = _compute_risk(human_comment=human_comment, element_type=element_type)
    reversibility = True  # Originals are always preserved in the IR layer
    scope = _compute_scope(human_comment=human_comment)
    evidence = _build_evidence(
        element_type=element_type,
        finding_severity=finding_severity,
        finding_criterion=finding_criterion,
        compliance_impact=compliance_impact,
        risk=risk,
        scope=scope,
    )

    # Recommendation: approve unless compliance impact is negative OR risk is high
    if compliance_impact == "negative" or risk == "high":
        recommendation = "reject"
        reason_parts: list[str] = []
        if compliance_impact == "negative":
            reason_parts.append("change would override an already-approved item")
        if risk == "high":
            if element_type == "table":
                reason_parts.append(
                    "table structural changes carry high risk of breaking reading order"
                )
            elif _GLOBAL_SCOPE_RE.search(human_comment):
                reason_parts.append(
                    "global-scope change requires admin review before propagation"
                )
            else:
                reason_parts.append("risk level is high")
        reason = "Rejected: " + "; ".join(reason_parts) + "."
    else:
        recommendation = "approve"
        reason_parts = []
        if compliance_impact == "positive":
            reason_parts.append(
                f"addresses {finding_severity} WCAG {finding_criterion} finding"
            )
        else:
            reason_parts.append("no negative compliance impact detected")
        reason_parts.append(f"risk is {risk}")
        reason_parts.append("change is reversible")
        reason = "Approved: " + "; ".join(reason_parts) + "."

    result: dict[str, Any] = {
        "compliance_impact": compliance_impact,
        "risk": risk,
        "reversibility": reversibility,
        "scope": scope,
        "evidence": evidence,
        "recommendation": recommendation,
        "reason": reason,
    }

    logger.debug(
        "evaluate_proposal: element_type=%s recommendation=%s risk=%s compliance_impact=%s",
        element_type,
        recommendation,
        risk,
        compliance_impact,
    )

    return result


def apply_proposal(proposal: dict, db: Any) -> dict:
    """Apply an approved proposal to its HITLReviewItem in the database.

    Steps:
        1. Retrieve the current review_item from the database.
        2. Snapshot the current state into the audit trail before modifying.
        3. Apply the proposed change (update reviewer_edit, reviewer_decision,
           reviewed_at, reviewed_by).
        4. Persist the updated item.
        5. Return the updated state dict.

    Args:
        proposal: Dict containing at minimum:
                    - review_item_id: str
                    - new_value:      str  (the edited content to apply)
                    - reviewer_id:    str  (who is making the change)
                    - evaluator_result: dict (output of evaluate_proposal)
        db:       Database object with methods:
                    - get_review_item(item_id) -> dict | None
                    - save_review_item(item: dict) -> None
                    - log_audit_event(event: dict) -> None

    Returns:
        Updated review_item dict.

    Raises:
        ValueError: if the review_item_id is not found or proposal is missing keys.
        RuntimeError: if the database operation fails.
    """
    item_id: str = proposal.get("review_item_id", "")
    new_value: str = proposal.get("new_value", "")
    reviewer_id: str = proposal.get("reviewer_id", "unknown")

    if not item_id:
        raise ValueError("proposal must contain 'review_item_id'")

    # Step 1: Retrieve current item
    item = db.get_review_item(item_id)
    if item is None:
        raise ValueError(f"HITLReviewItem not found: {item_id}")

    # Step 2: Snapshot current state for audit trail
    audit_event: dict[str, Any] = {
        "event_type": "proposal_applied",
        "review_item_id": item_id,
        "document_id": item.get("document_id"),
        "reviewer_id": reviewer_id,
        "before": {
            "reviewer_decision": item.get("reviewer_decision"),
            "reviewer_edit": item.get("reviewer_edit"),
            "reviewed_at": item.get("reviewed_at"),
            "reviewed_by": item.get("reviewed_by"),
        },
        "evaluator_result": proposal.get("evaluator_result", {}),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Step 3: Apply the change
    item["reviewer_edit"] = new_value
    item["reviewer_decision"] = "edit"
    item["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    item["reviewed_by"] = reviewer_id

    audit_event["after"] = {
        "reviewer_decision": item["reviewer_decision"],
        "reviewer_edit": item["reviewer_edit"],
        "reviewed_at": item["reviewed_at"],
        "reviewed_by": item["reviewed_by"],
    }

    # Step 4: Persist
    try:
        db.save_review_item(item)
        db.log_audit_event(audit_event)
    except Exception as exc:
        logger.exception("Failed to persist proposal for item_id=%s", item_id)
        raise RuntimeError(f"Database write failed for review_item {item_id}: {exc}") from exc

    logger.info(
        "Applied proposal: item_id=%s reviewer_id=%s new_value_len=%d",
        item_id,
        reviewer_id,
        len(new_value),
    )

    return item


def rollback_proposal(proposal: dict, db: Any) -> None:
    """Restore a HITLReviewItem to its pre-proposal state.

    Reads the 'before' snapshot stored in the audit trail and writes it
    back to the review_item. A rollback audit event is logged.

    Args:
        proposal: Dict containing:
                    - review_item_id:  str
                    - reviewer_id:     str
                    - before_snapshot: dict  (state to restore — keys: reviewer_decision,
                                             reviewer_edit, reviewed_at, reviewed_by)
        db:       Database object with methods:
                    - get_review_item(item_id) -> dict | None
                    - save_review_item(item: dict) -> None
                    - log_audit_event(event: dict) -> None

    Raises:
        ValueError: if the review_item_id is not found or before_snapshot is missing.
        RuntimeError: if the database write fails.
    """
    item_id: str = proposal.get("review_item_id", "")
    reviewer_id: str = proposal.get("reviewer_id", "unknown")
    before: dict = proposal.get("before_snapshot", {})

    if not item_id:
        raise ValueError("proposal must contain 'review_item_id'")
    if not before:
        raise ValueError("proposal must contain 'before_snapshot' to rollback")

    # Retrieve current item
    item = db.get_review_item(item_id)
    if item is None:
        raise ValueError(f"HITLReviewItem not found for rollback: {item_id}")

    # Capture state before rollback for the audit entry
    current_state = {
        "reviewer_decision": item.get("reviewer_decision"),
        "reviewer_edit": item.get("reviewer_edit"),
        "reviewed_at": item.get("reviewed_at"),
        "reviewed_by": item.get("reviewed_by"),
    }

    # Restore the pre-proposal state
    item["reviewer_decision"] = before.get("reviewer_decision")
    item["reviewer_edit"] = before.get("reviewer_edit")
    item["reviewed_at"] = before.get("reviewed_at")
    item["reviewed_by"] = before.get("reviewed_by")

    audit_event: dict[str, Any] = {
        "event_type": "proposal_rolled_back",
        "review_item_id": item_id,
        "document_id": item.get("document_id"),
        "reviewer_id": reviewer_id,
        "rolled_back_from": current_state,
        "restored_to": before,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        db.save_review_item(item)
        db.log_audit_event(audit_event)
    except Exception as exc:
        logger.exception("Failed to persist rollback for item_id=%s", item_id)
        raise RuntimeError(f"Rollback write failed for review_item {item_id}: {exc}") from exc

    logger.info("Rolled back proposal: item_id=%s reviewer_id=%s", item_id, reviewer_id)
