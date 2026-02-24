"""Tests for the rules engine module."""

from __future__ import annotations

import pytest

from services.common.rules_engine import RulesEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_rule(
    trigger: str = "table:missing_headers",
    action_type: str = "add_scope",
    action_value: str = "col",
    confidence: float = 0.8,
    version: int = 1,
    rule_id: str = "rule-001",
) -> dict:
    return {
        "id": rule_id,
        "trigger_pattern": trigger,
        "action": {"type": action_type, "value": action_value},
        "confidence": confidence,
        "version": version,
        "status": "active",
    }


def _make_element(
    elem_type: str = "table",
    content: str = "Budget Table",
    attributes: dict | None = None,
) -> dict:
    return {
        "type": elem_type,
        "content": content,
        "attributes": attributes or {},
    }


# ---------------------------------------------------------------------------
# Engine initialization
# ---------------------------------------------------------------------------


class TestEngineInit:
    def test_init_with_no_rules(self):
        engine = RulesEngine(rules=[])
        assert engine._rules == []

    def test_init_with_none_rules(self):
        engine = RulesEngine(rules=None)
        assert engine._rules == []

    def test_init_drops_invalid_rules(self):
        valid = _make_rule()
        invalid = {"id": "bad", "trigger_pattern": "no-colon", "action": {}}
        engine = RulesEngine(rules=[valid, invalid])
        assert len(engine._rules) == 1

    def test_init_drops_low_confidence_rules(self):
        low = _make_rule(confidence=0.3, rule_id="low")
        high = _make_rule(confidence=0.9, rule_id="high")
        engine = RulesEngine(rules=[low, high])
        assert len(engine._rules) == 1
        assert engine._rules[0]["id"] == "high"

    def test_init_sorts_by_version(self):
        r1 = _make_rule(version=3, rule_id="v3")
        r2 = _make_rule(version=1, rule_id="v1")
        r3 = _make_rule(version=2, rule_id="v2")
        engine = RulesEngine(rules=[r1, r2, r3])
        versions = [r["version"] for r in engine._rules]
        assert versions == [1, 2, 3]


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------


class TestRuleMatching:
    def test_table_missing_headers_matches(self):
        rule = _make_rule(trigger="table:missing_headers")
        engine = RulesEngine(rules=[rule])
        element = _make_element("table", attributes={})
        assert engine._match_rule(rule, element) is True

    def test_table_missing_headers_no_match_when_headers_present(self):
        rule = _make_rule(trigger="table:missing_headers")
        engine = RulesEngine(rules=[rule])
        element = _make_element("table", attributes={"headers": ["A", "B"]})
        assert engine._match_rule(rule, element) is False

    def test_image_no_alt_matches(self):
        rule = _make_rule(trigger="image:no_alt", action_type="set_alt", action_value="placeholder")
        engine = RulesEngine(rules=[rule])
        element = _make_element("image", attributes={})
        assert engine._match_rule(rule, element) is True

    def test_image_no_alt_no_match_when_alt_present(self):
        rule = _make_rule(trigger="image:no_alt")
        engine = RulesEngine(rules=[rule])
        element = _make_element("image", attributes={"alt": "A description"})
        assert engine._match_rule(rule, element) is False

    def test_heading_skip_level_matches(self):
        rule = _make_rule(trigger="heading:skip_level", action_type="set_heading", action_value="2")
        engine = RulesEngine(rules=[rule])
        element = _make_element("heading", attributes={"level": 4, "prev_level": 1})
        assert engine._match_rule(rule, element) is True

    def test_heading_skip_level_no_match_sequential(self):
        rule = _make_rule(trigger="heading:skip_level")
        engine = RulesEngine(rules=[rule])
        element = _make_element("heading", attributes={"level": 2, "prev_level": 1})
        assert engine._match_rule(rule, element) is False

    def test_paragraph_empty_matches(self):
        rule = _make_rule(trigger="paragraph:empty", action_type="remove_element")
        engine = RulesEngine(rules=[rule])
        element = _make_element("paragraph", content="   ", attributes={})
        assert engine._match_rule(rule, element) is True

    def test_paragraph_empty_no_match_with_content(self):
        rule = _make_rule(trigger="paragraph:empty")
        engine = RulesEngine(rules=[rule])
        element = _make_element("paragraph", content="Some text", attributes={})
        assert engine._match_rule(rule, element) is False

    def test_type_mismatch_never_matches(self):
        rule = _make_rule(trigger="table:missing_headers")
        engine = RulesEngine(rules=[rule])
        element = _make_element("image", attributes={})
        assert engine._match_rule(rule, element) is False


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------


class TestActionExecution:
    def test_add_scope_action(self):
        rule = _make_rule(action_type="add_scope", action_value="col")
        engine = RulesEngine(rules=[rule])
        element = _make_element("table", attributes={})
        modified = engine._apply_action(rule, element)
        assert modified["attributes"]["scope"] == "col"

    def test_set_alt_action(self):
        rule = _make_rule(
            trigger="image:no_alt", action_type="set_alt",
            action_value="Descriptive alt text",
        )
        engine = RulesEngine(rules=[rule])
        element = _make_element("image", attributes={})
        modified = engine._apply_action(rule, element)
        assert modified["attributes"]["alt"] == "Descriptive alt text"

    def test_remove_element_action(self):
        rule = _make_rule(
            trigger="paragraph:empty", action_type="remove_element",
        )
        engine = RulesEngine(rules=[rule])
        element = _make_element("paragraph", content="", attributes={})
        modified = engine._apply_action(rule, element)
        assert modified["attributes"]["_remove"] is True

    def test_set_heading_action(self):
        rule = _make_rule(
            trigger="heading:skip_level", action_type="set_heading", action_value="2",
        )
        engine = RulesEngine(rules=[rule])
        element = _make_element("heading", attributes={"level": 4})
        modified = engine._apply_action(rule, element)
        assert modified["attributes"]["level"] == 2

    def test_action_does_not_mutate_original(self):
        rule = _make_rule(action_type="add_scope", action_value="col")
        engine = RulesEngine(rules=[rule])
        original_attrs = {}
        element = _make_element("table", attributes=original_attrs)
        engine._apply_action(rule, element)
        assert "scope" not in original_attrs


# ---------------------------------------------------------------------------
# Full apply_rules
# ---------------------------------------------------------------------------


class TestApplyRules:
    def test_apply_rules_modifies_matching_elements(self):
        rule = _make_rule(trigger="table:missing_headers", action_type="add_scope", action_value="col")
        engine = RulesEngine(rules=[rule])
        elements = [
            _make_element("table", attributes={}),
            _make_element("paragraph", content="Text"),
        ]
        modified, log = engine.apply_rules(elements, document_id="doc-1")
        assert modified[0]["attributes"]["scope"] == "col"
        assert "scope" not in modified[1]["attributes"]

    def test_apply_rules_returns_execution_log(self):
        rule = _make_rule(trigger="table:missing_headers")
        engine = RulesEngine(rules=[rule])
        elements = [_make_element("table", attributes={})]
        _, log = engine.apply_rules(elements, document_id="doc-1")
        assert len(log) > 0
        assert log[0]["rule_id"] == "rule-001"
        assert log[0]["matched"] is True

    def test_apply_rules_with_no_matching_elements(self):
        rule = _make_rule(trigger="image:no_alt")
        engine = RulesEngine(rules=[rule])
        elements = [_make_element("paragraph", content="Text")]
        modified, log = engine.apply_rules(elements, document_id="doc-1")
        assert modified[0]["content"] == "Text"
        matched_entries = [e for e in log if e["matched"]]
        assert len(matched_entries) == 0

    def test_multiple_rules_applied_in_order(self):
        r1 = _make_rule(
            trigger="image:no_alt", action_type="set_alt",
            action_value="v1", version=1, rule_id="r1",
        )
        r2 = _make_rule(
            trigger="image:no_alt", action_type="set_alt",
            action_value="v2", version=2, rule_id="r2",
        )
        engine = RulesEngine(rules=[r2, r1])  # Should sort by version
        elements = [_make_element("image", attributes={})]
        modified, _ = engine.apply_rules(elements, document_id="doc-1")
        # r1 runs first (v1), then r2 (v2) overwrites — but r2 only matches if alt is empty
        # After r1, alt is "v1" (non-empty), so r2 should NOT match
        assert modified[0]["attributes"]["alt"] == "v1"


# ---------------------------------------------------------------------------
# Rule validation
# ---------------------------------------------------------------------------


class TestRuleValidation:
    def test_invalid_pattern_no_colon(self):
        assert RulesEngine._is_valid_rule({"trigger_pattern": "no_colon", "action": {"type": "x"}, "confidence": 0.8}) is False

    def test_invalid_element_type(self):
        assert RulesEngine._is_valid_rule({"trigger_pattern": "unknown_type:empty", "action": {"type": "x"}, "confidence": 0.8}) is False

    def test_invalid_condition(self):
        assert RulesEngine._is_valid_rule({"trigger_pattern": "table:unknown_condition", "action": {"type": "x"}, "confidence": 0.8}) is False

    def test_missing_action_type(self):
        assert RulesEngine._is_valid_rule({"trigger_pattern": "table:missing_headers", "action": {}, "confidence": 0.8}) is False

    def test_valid_rule_passes(self):
        rule = _make_rule()
        assert RulesEngine._is_valid_rule(rule) is True
