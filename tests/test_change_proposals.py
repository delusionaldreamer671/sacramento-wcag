"""Tests for the change proposal evaluation module."""

from __future__ import annotations

import pytest

from services.common.change_evaluator import (
    _compute_compliance_impact,
    _compute_risk,
    _compute_scope,
    evaluate_proposal,
)


# ---------------------------------------------------------------------------
# Compliance Impact
# ---------------------------------------------------------------------------


class TestComplianceImpact:
    def test_positive_for_critical_finding_with_comment(self):
        result = _compute_compliance_impact(
            human_comment="Add descriptive alt text for the chart",
            finding_severity="critical",
            finding_criterion="1.1.1",
        )
        assert result == "positive"

    def test_positive_for_serious_finding_with_comment(self):
        result = _compute_compliance_impact(
            human_comment="Fix table headers",
            finding_severity="serious",
            finding_criterion="1.3.1",
        )
        assert result == "positive"

    def test_negative_for_override_of_approved_item(self):
        result = _compute_compliance_impact(
            human_comment="Change this anyway",
            finding_severity="moderate",
            finding_criterion="1.1.1",
            reviewer_decision="approve",
        )
        assert result == "negative"

    def test_neutral_for_moderate_finding(self):
        result = _compute_compliance_impact(
            human_comment="Minor tweak",
            finding_severity="moderate",
            finding_criterion="1.1.1",
        )
        assert result == "neutral"

    def test_neutral_for_empty_comment(self):
        result = _compute_compliance_impact(
            human_comment="",
            finding_severity="critical",
            finding_criterion="1.1.1",
        )
        assert result == "neutral"

    def test_neutral_for_no_criterion(self):
        result = _compute_compliance_impact(
            human_comment="Add alt text",
            finding_severity="critical",
            finding_criterion=None,
        )
        assert result == "neutral"


# ---------------------------------------------------------------------------
# Risk Assessment
# ---------------------------------------------------------------------------


class TestRiskAssessment:
    def test_high_risk_for_table_changes(self):
        assert _compute_risk("Fix header scope", "table") == "high"

    def test_high_risk_for_global_keywords(self):
        assert _compute_risk("Apply this to all documents", "image") == "high"
        assert _compute_risk("Always use this alt text", "paragraph") == "high"

    def test_low_risk_for_image_alt(self):
        assert _compute_risk("Update alt text", "image") == "low"

    def test_medium_risk_for_heading(self):
        assert _compute_risk("Fix heading level", "heading") == "medium"

    def test_medium_risk_for_paragraph(self):
        assert _compute_risk("Update content", "paragraph") == "medium"


# ---------------------------------------------------------------------------
# Scope Detection
# ---------------------------------------------------------------------------


class TestScopeDetection:
    def test_global_scope_with_all_documents(self):
        assert _compute_scope("Apply to all documents") == "global_rule"

    def test_global_scope_with_always(self):
        assert _compute_scope("Always use this format") == "global_rule"

    def test_global_scope_with_every(self):
        assert _compute_scope("Do this for every image") == "global_rule"

    def test_single_doc_for_normal_comment(self):
        assert _compute_scope("Fix this alt text") == "single_doc"

    def test_single_doc_for_empty_comment(self):
        assert _compute_scope("") == "single_doc"


# ---------------------------------------------------------------------------
# Full Evaluation
# ---------------------------------------------------------------------------


class TestEvaluateProposal:
    def test_approve_for_positive_low_risk(self):
        result = evaluate_proposal(
            human_comment="Add descriptive alt text for this chart image",
            element_type="image",
            finding_severity="critical",
            finding_criterion="1.1.1",
        )
        assert result["recommendation"] == "approve"
        assert result["compliance_impact"] == "positive"
        assert result["risk"] == "low"
        assert result["reversibility"] is True
        assert result["scope"] == "single_doc"
        assert "Approved:" in result["reason"]

    def test_reject_for_high_risk_table(self):
        result = evaluate_proposal(
            human_comment="Restructure the entire table",
            element_type="table",
            finding_severity="serious",
            finding_criterion="1.3.1",
        )
        assert result["recommendation"] == "reject"
        assert result["risk"] == "high"
        assert "Rejected:" in result["reason"]

    def test_reject_for_global_scope(self):
        result = evaluate_proposal(
            human_comment="Always add this alt text to all images",
            element_type="image",
            finding_severity="critical",
            finding_criterion="1.1.1",
        )
        assert result["recommendation"] == "reject"
        assert result["scope"] == "global_rule"

    def test_reject_for_negative_compliance_impact(self):
        result = evaluate_proposal(
            human_comment="I want to change this back",
            element_type="image",
            finding_severity="minor",
            finding_criterion="1.1.1",
            reviewer_decision="approve",
        )
        assert result["recommendation"] == "reject"
        assert result["compliance_impact"] == "negative"

    def test_evaluation_has_all_required_keys(self):
        result = evaluate_proposal(
            human_comment="Test comment",
            element_type="paragraph",
        )
        expected_keys = {
            "compliance_impact", "risk", "reversibility", "scope",
            "evidence", "recommendation", "reason",
        }
        assert set(result.keys()) == expected_keys

    def test_evidence_string_format(self):
        result = evaluate_proposal(
            human_comment="Fix alt text",
            element_type="image",
            finding_severity="critical",
            finding_criterion="1.1.1",
        )
        assert "element_type=image" in result["evidence"]
        assert "finding_severity=critical" in result["evidence"]
        assert "wcag_criterion=1.1.1" in result["evidence"]
