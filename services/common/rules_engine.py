"""Rules engine — deterministic execution of learned remediation rules.

Rules are stored in the rules_ledger SQLite table (managed by the database
layer in services.common.database). Active rules are loaded and applied in
ascending version order between extraction and HTML build.

Design principles:
  - Fully deterministic: no AI calls, no randomness.
  - Idempotent: applying the same rule set twice produces the same output.
  - Auditable: every rule application is logged in the execution_log.
  - Isolated: each rule mutates only the single element it matched.

Rule schema (stored as dict / database row):
    id:              str   — UUID
    trigger_pattern: str   — "<element_type>:<condition>"  e.g. "table:missing_headers"
    action:          dict  — {"type": "<action_type>", "value": "<optional_value>"}
    confidence:      float — 0.0-1.0 (rules with confidence < 0.7 are candidates, not active)
    status:          str   — "candidate" | "active" | "retired"
    version:         int   — monotonically increasing per rule_id
    validated_docs:  int   — number of documents this rule was successfully applied to

Supported trigger_pattern conditions:
    table:missing_headers  — table element whose attributes lack "headers" or "th"
    image:no_alt           — image element with empty or missing alt attribute
    heading:skip_level     — heading whose level skips more than one step from previous
    paragraph:empty        — paragraph element with no non-whitespace content

Supported action types:
    add_scope      — add scope attribute to th elements within a table
    set_alt        — set or replace the alt attribute on an image
    remove_element — flag the element for removal from output
    set_heading    — adjust heading level (value = target level int as str)

Usage:

    from services.common.rules_engine import RulesEngine

    engine = RulesEngine(rules=active_rules_from_db)
    modified_elements, log = engine.apply_rules(elements, document_id="doc-123")
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_PROMOTION_DOCS = 3       # Rule must be validated on this many docs before promotion
_MIN_ACTIVE_CONFIDENCE = 0.7  # Rules below this threshold stay as candidates

# ---------------------------------------------------------------------------
# Supported trigger conditions
# ---------------------------------------------------------------------------

_VALID_CONDITIONS: frozenset[str] = frozenset(
    [
        "missing_headers",  # for table
        "no_alt",           # for image
        "skip_level",       # for heading
        "empty",            # for paragraph
    ]
)

_VALID_ELEMENT_TYPES: frozenset[str] = frozenset(
    ["table", "image", "heading", "paragraph", "link", "form_field"]
)

# ---------------------------------------------------------------------------
# RulesEngine
# ---------------------------------------------------------------------------


class RulesEngine:
    """Apply a sorted list of remediation rules to extracted PDF elements.

    Args:
        rules: List of rule dicts. If None, the engine operates with no rules
               (useful for testing — pass rules explicitly). When the database
               layer is available, pass the result of db.get_active_rules().
    """

    def __init__(self, rules: Optional[list[dict]] = None) -> None:
        if rules is None:
            rules = []

        # Validate and sort by ascending version (lower version = earlier, takes precedence)
        self._rules: list[dict] = sorted(
            [r for r in rules if self._is_valid_rule(r)],
            key=lambda r: int(r.get("version", 0)),
        )

        if len(self._rules) != len(rules):
            dropped = len(rules) - len(self._rules)
            logger.warning(
                "RulesEngine: dropped %d invalid rule(s) during init. "
                "Check trigger_pattern format and status field.",
                dropped,
            )

        logger.info("RulesEngine initialised with %d active rule(s).", len(self._rules))

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def apply_rules(
        self, elements: list[dict], document_id: str
    ) -> tuple[list[dict], list[dict]]:
        """Apply all active rules to the element list in version order.

        Each element is checked against every rule. The first rule that
        matches and is successfully applied modifies the element; subsequent
        rules still run (one match does not short-circuit remaining rules,
        because multiple conditions can co-exist on the same element).

        Args:
            elements:    List of element dicts, each with at minimum:
                           - "type": str  (element_type)
                           - "attributes": dict
                           - "content": str
            document_id: The document being processed (used for audit logging).

        Returns:
            Tuple of:
              - modified_elements: The (potentially mutated) element list.
              - execution_log:     List of log entry dicts, one per rule evaluated.
        """
        execution_log: list[dict] = []

        for rule in self._rules:
            rule_id = rule.get("id", "unknown")
            matched_elements: list[str] = []
            changes: list[dict] = []

            for idx, element in enumerate(elements):
                matched = self._match_rule(rule, element)

                log_entry: dict[str, Any] = {
                    "rule_id": rule_id,
                    "element_index": idx,
                    "element_type": element.get("type", "unknown"),
                    "matched": matched,
                    "changes": [],
                }

                if matched:
                    element_before = dict(element.get("attributes", {}))
                    elements[idx] = self._apply_action(rule, element)
                    element_after = dict(elements[idx].get("attributes", {}))

                    change_record: dict[str, Any] = {
                        "element_index": idx,
                        "attributes_before": element_before,
                        "attributes_after": element_after,
                    }
                    log_entry["changes"].append(change_record)
                    changes.append(change_record)
                    matched_elements.append(str(idx))

                execution_log.append(log_entry)

            if matched_elements:
                logger.debug(
                    "Rule %s matched %d element(s) in document %s",
                    rule_id,
                    len(matched_elements),
                    document_id,
                )

        return elements, execution_log

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _match_rule(self, rule: dict, element: dict) -> bool:
        """Check whether a rule's trigger_pattern matches an element.

        Pattern format: "<element_type>:<condition>"
        Both parts must match for the rule to fire.

        Matching logic per condition:
            missing_headers — element_type is "table" AND "headers" key absent
                              from attributes (or attributes["headers"] is empty)
            no_alt          — element_type is "image" AND attributes.get("alt", "") is empty
            skip_level      — element_type is "heading" AND attributes contain
                              "level" and "prev_level" AND the gap > 1
            empty           — element.get("content", "").strip() == ""

        Returns True if the element matches, False otherwise.
        """
        pattern = rule.get("trigger_pattern", "")
        parts = pattern.split(":", 1)
        if len(parts) != 2:
            return False

        rule_type, condition = parts[0].strip(), parts[1].strip()
        elem_type: str = element.get("type", "").strip()
        attributes: dict = element.get("attributes", {})
        content: str = element.get("content", "")

        # Type must match first
        if elem_type != rule_type:
            return False

        # Condition matching
        if condition == "missing_headers":
            # Table has no headers list, or headers list is empty
            headers = attributes.get("headers", None)
            if headers is None:
                return True
            if isinstance(headers, list) and len(headers) == 0:
                return True
            if isinstance(headers, str) and not headers.strip():
                return True
            return False

        if condition == "no_alt":
            # Image with empty or absent alt attribute
            alt = attributes.get("alt", None)
            if alt is None:
                return True
            if isinstance(alt, str) and not alt.strip():
                return True
            return False

        if condition == "skip_level":
            # Heading that jumps more than one level.
            # MEDIUM-5.18: prev_level must be present in the element's attributes
            # for this rule to fire. If prev_level is absent, the rule is inert
            # (defaults to level, gap = 0). Log a warning so operators know the
            # rule is not producing matches — they must ensure the extraction
            # pipeline populates prev_level on every heading element.
            try:
                level = int(attributes.get("level", 0))
                raw_prev = attributes.get("prev_level")
                if raw_prev is None:
                    logger.warning(
                        "_match_rule: skip_level rule triggered on heading "
                        "(level=%d) but element has no 'prev_level' attribute — "
                        "rule will not fire. Ensure the extraction pipeline sets "
                        "'prev_level' on all heading elements.",
                        level,
                    )
                    return False
                prev_level = int(raw_prev)
            except (ValueError, TypeError):
                return False
            return (level - prev_level) > 1

        if condition == "empty":
            # Element with no meaningful text content
            return not content.strip()

        # Unknown condition — no match
        logger.warning("Unknown rule condition '%s' in pattern '%s'", condition, pattern)
        return False

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    def _apply_action(self, rule: dict, element: dict) -> dict:
        """Apply the rule's action to the element, returning a modified copy.

        The original element dict is not mutated — a shallow copy is made and
        only the relevant nested dict (attributes) is updated.

        Supported action types:
            add_scope      — sets attributes["scope"] to action["value"]
            set_alt        — sets attributes["alt"] to action["value"]
            remove_element — sets attributes["_remove"] = True (caller must filter)
            set_heading    — sets attributes["level"] to int(action["value"])

        Returns:
            Modified element dict.
        """
        action: dict = rule.get("action", {})
        action_type: str = action.get("type", "")
        action_value: str = str(action.get("value", ""))

        # Shallow copy of the element; deep copy only the attributes dict
        modified = dict(element)
        modified["attributes"] = dict(element.get("attributes", {}))

        if action_type == "add_scope":
            modified["attributes"]["scope"] = action_value
            logger.debug(
                "Applied add_scope=%s to element type=%s", action_value, element.get("type")
            )

        elif action_type == "set_alt":
            modified["attributes"]["alt"] = action_value
            logger.debug(
                "Applied set_alt='%s' to image element", action_value
            )

        elif action_type == "remove_element":
            # Signal to the HTML builder to skip this element
            modified["attributes"]["_remove"] = True
            logger.debug("Flagged element for removal: type=%s", element.get("type"))

        elif action_type == "set_heading":
            try:
                new_level = int(action_value)
                if 1 <= new_level <= 6:
                    modified["attributes"]["level"] = new_level
                    logger.debug("Applied set_heading level=%d", new_level)
                else:
                    logger.warning(
                        "set_heading value %d out of range [1-6] — skipping", new_level
                    )
            except (ValueError, TypeError):
                logger.warning(
                    "set_heading action has non-integer value '%s' — skipping", action_value
                )

        else:
            logger.warning(
                "Unknown action type '%s' in rule %s — element unchanged",
                action_type,
                rule.get("id", "unknown"),
            )

        return modified

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_valid_rule(rule: dict) -> bool:
        """Return True if the rule dict has the required fields and valid values."""
        if not isinstance(rule, dict):
            return False

        pattern = rule.get("trigger_pattern", "")
        if ":" not in pattern:
            logger.warning("Invalid rule trigger_pattern (no colon): '%s'", pattern)
            return False

        rule_type, condition = pattern.split(":", 1)
        if rule_type not in _VALID_ELEMENT_TYPES:
            logger.warning("Unrecognised element type in rule pattern: '%s'", rule_type)
            return False

        if condition not in _VALID_CONDITIONS:
            logger.warning("Unrecognised condition in rule pattern: '%s'", condition)
            return False

        action = rule.get("action", {})
        if not isinstance(action, dict) or "type" not in action:
            logger.warning("Rule missing action.type: %s", rule.get("id"))
            return False

        confidence = rule.get("confidence", 0.0)
        try:
            if float(confidence) < _MIN_ACTIVE_CONFIDENCE:
                logger.debug(
                    "Rule %s has confidence %.2f below threshold %.2f — skipping",
                    rule.get("id", "unknown"),
                    float(confidence),
                    _MIN_ACTIVE_CONFIDENCE,
                )
                return False
        except (ValueError, TypeError):
            return False

        return True

    # ------------------------------------------------------------------
    # Rule lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def promote_rule(rule_id: str, db: Any) -> bool:
        """Promote a candidate rule to active status if validated on 3+ documents.

        A candidate rule is promoted when:
          - Its status is "candidate"
          - Its validated_docs count >= _MIN_PROMOTION_DOCS (default: 3)
          - Its confidence >= _MIN_ACTIVE_CONFIDENCE (default: 0.7)

        Args:
            rule_id: The UUID of the candidate rule to evaluate for promotion.
            db:      Database object with methods:
                       - get_rule(rule_id) -> dict | None
                       - save_rule(rule: dict) -> None

        Returns:
            True if the rule was promoted, False if promotion criteria not met
            or rule not found.

        Raises:
            RuntimeError: if the database write fails during promotion.
        """
        rule = db.get_rule(rule_id)
        if rule is None:
            logger.warning("promote_rule: rule_id=%s not found", rule_id)
            return False

        current_status = rule.get("status", "candidate")
        if current_status == "active":
            logger.info("Rule %s is already active — skipping promotion", rule_id)
            return False

        if current_status == "retired":
            logger.warning("Rule %s is retired — cannot promote", rule_id)
            return False

        validated_docs = int(rule.get("validated_docs", 0))
        confidence = float(rule.get("confidence", 0.0))

        if validated_docs < _MIN_PROMOTION_DOCS:
            logger.info(
                "Rule %s has %d validated docs (need %d) — not promoted",
                rule_id,
                validated_docs,
                _MIN_PROMOTION_DOCS,
            )
            return False

        if confidence < _MIN_ACTIVE_CONFIDENCE:
            logger.info(
                "Rule %s has confidence %.2f (need %.2f) — not promoted",
                rule_id,
                confidence,
                _MIN_ACTIVE_CONFIDENCE,
            )
            return False

        # Promote the rule
        rule["status"] = "active"
        try:
            db.save_rule(rule)
        except Exception as exc:
            raise RuntimeError(f"Failed to promote rule {rule_id}: {exc}") from exc

        logger.info(
            "Promoted rule %s to active (validated_docs=%d, confidence=%.2f)",
            rule_id,
            validated_docs,
            confidence,
        )
        return True
