"""WCAG Technique Coverage Tests — Sacramento County PDF Remediation Pipeline.

Tests the internal consistency and coverage of:
  - wcag_techniques.py (PDF_TECHNIQUES, FAILURE_TECHNIQUES, cross-reference maps)
  - wcag_rules.py (WCAG_RULES_LEDGER, technique field cross-references)
  - wcag_checker.py (CHECK_DISPATCH, run_full_audit, findings_to_proposals, audit_summary_dict)

Sections:
  1. Registry Integrity Tests
  2. Cross-Reference Consistency Tests
  3. Checker Dispatch Completeness Tests
  4. Audit Execution Tests (with mock IRDocument)
  5. Technique Reference in Findings Tests
  6. Specific Enrichment Tests

Note on Section 5: The technique-reference-in-evidence tests will fail until
wcag_checker.py is updated to embed technique IDs (e.g. "PDF1", "PDF6") into
the ``evidence`` field of each finding. That is expected and documented inline.
"""

from __future__ import annotations

import pytest

from services.common.wcag_techniques import (
    CRITERION_TO_FAILURE_TECHNIQUES,
    CRITERION_TO_PDF_TECHNIQUES,
    FAILURE_TECHNIQUES,
    PDF_TECHNIQUES,
)
from services.common.wcag_rules import (
    FindingStatus,
    WCAG_RULES_LEDGER,
)
from services.common.wcag_checker import (
    CHECK_DISPATCH,
    audit_summary_dict,
    findings_to_proposals,
    run_full_audit,
)
from services.common.ir import BlockType, BoundingBox, IRBlock, IRDocument, IRPage


# ---------------------------------------------------------------------------
# Shared IR document helpers
# ---------------------------------------------------------------------------


def _make_minimal_doc() -> IRDocument:
    """Document with no content — most checks will return PASS or NOT_APPLICABLE."""
    return IRDocument(
        document_id="test-empty",
        filename="test.pdf",
        page_count=1,
        pages=[IRPage(page_num=1, blocks=[])],
        language="en",
        metadata={"title": "Test Document", "language": "en"},
    )


def _make_rich_doc() -> IRDocument:
    """Document with images, tables, headings, lists, and links.

    Designed to trigger most check functions. Page 2 has an image with empty
    alt text so that 1.1.1 fires a FAIL finding.
    """
    return IRDocument(
        document_id="test-rich",
        filename="test.pdf",
        page_count=2,
        pages=[
            IRPage(
                page_num=1,
                blocks=[
                    IRBlock(
                        block_type=BlockType.HEADING,
                        content="Introduction",
                        page_num=1,
                        attributes={"level": 1},
                    ),
                    IRBlock(
                        block_type=BlockType.PARAGRAPH,
                        content=(
                            "This report covers data. "
                            "See https://example.com for more info."
                        ),
                        page_num=1,
                    ),
                    IRBlock(
                        block_type=BlockType.IMAGE,
                        content="",
                        page_num=1,
                        attributes={
                            "alt": "Map of Sacramento County showing districts",
                            "src": "data:image/png;base64,abc",
                            "image_id": "img_p1_i0",
                        },
                    ),
                    IRBlock(
                        block_type=BlockType.TABLE,
                        content="Data Table",
                        page_num=1,
                        attributes={
                            "headers": ["Year", "Population"],
                            "rows": [["2020", "1500000"], ["2021", "1520000"]],
                        },
                    ),
                    IRBlock(
                        block_type=BlockType.LIST,
                        content="",
                        page_num=1,
                        attributes={"items": ["Item 1", "Item 2", "Item 3"]},
                    ),
                ],
            ),
            IRPage(
                page_num=2,
                blocks=[
                    IRBlock(
                        block_type=BlockType.HEADING,
                        content="Details",
                        page_num=2,
                        attributes={"level": 2},
                    ),
                    IRBlock(
                        block_type=BlockType.IMAGE,
                        content="",
                        page_num=2,
                        attributes={
                            "alt": "",
                            "src": "data:image/png;base64,def",
                            "image_id": "img_p2_i0",
                        },
                    ),
                ],
            ),
        ],
        language="en",
        metadata={"title": "Sacramento County Report", "language": "en"},
    )


# ===========================================================================
# SECTION 1: Registry Integrity Tests
# ===========================================================================


class TestPDFTechniquesRegistry:
    """Verify count, IDs, and field completeness for PDF_TECHNIQUES."""

    def test_pdf_techniques_count(self):
        """Exactly 23 PDF techniques in registry."""
        assert len(PDF_TECHNIQUES) == 23

    def test_all_pdf_technique_ids_are_sequential(self):
        """PDF1 through PDF23 are all present in the registry."""
        for i in range(1, 24):
            assert f"PDF{i}" in PDF_TECHNIQUES, (
                f"PDF{i} is missing from PDF_TECHNIQUES"
            )

    def test_technique_criterion_lists_not_empty(self):
        """Every PDF technique has at least one WCAG criterion."""
        for tid, tech in PDF_TECHNIQUES.items():
            assert len(tech.wcag_criteria) > 0, (
                f"{tid} has no wcag_criteria"
            )

    def test_technique_titles_not_empty(self):
        """Every PDF technique has a non-empty title."""
        for tid, tech in PDF_TECHNIQUES.items():
            assert tech.title.strip(), f"{tid} has empty title"

    def test_technique_pipeline_relevance_valid_values(self):
        """All pipeline_relevance values are one of the allowed set."""
        valid = {"critical", "high", "medium", "low", "out_of_scope"}
        for tid, tech in PDF_TECHNIQUES.items():
            assert tech.pipeline_relevance in valid, (
                f"{tid}.pipeline_relevance = '{tech.pipeline_relevance}' is not in {valid}"
            )

    def test_technique_type_valid_values(self):
        """All technique_type values are 'sufficient' or 'advisory'."""
        for tid, tech in PDF_TECHNIQUES.items():
            assert tech.technique_type in {"sufficient", "advisory"}, (
                f"{tid}.technique_type = '{tech.technique_type}' is invalid"
            )


class TestFailureTechniquesRegistry:
    """Verify count, IDs, and field completeness for FAILURE_TECHNIQUES."""

    def test_failure_techniques_count(self):
        """Exactly 11 failure techniques in registry."""
        assert len(FAILURE_TECHNIQUES) == 11

    def test_all_failure_ids_present(self):
        """All expected failure technique IDs are present in the registry."""
        expected = {
            "F25", "F30", "F38", "F39", "F43",
            "F46", "F65", "F68", "F86", "F90", "F91",
        }
        assert set(FAILURE_TECHNIQUES.keys()) == expected

    def test_failure_criterion_lists_not_empty(self):
        """Every failure technique has at least one WCAG criterion."""
        for fid, fail in FAILURE_TECHNIQUES.items():
            assert len(fail.wcag_criteria) > 0, (
                f"{fid} has no wcag_criteria"
            )

    def test_failure_titles_not_empty(self):
        """Every failure technique has a non-empty title."""
        for fid, fail in FAILURE_TECHNIQUES.items():
            assert fail.title.strip(), f"{fid} has empty title"

    def test_failure_descriptions_not_empty(self):
        """Every failure technique has a non-empty description."""
        for fid, fail in FAILURE_TECHNIQUES.items():
            assert fail.description.strip(), (
                f"{fid} has empty description"
            )


# ===========================================================================
# SECTION 2: Cross-Reference Consistency Tests
# ===========================================================================


class TestCriterionToPDFCrossReference:
    """Verify bi-directional consistency of CRITERION_TO_PDF_TECHNIQUES."""

    def test_forward_all_referenced_techniques_exist(self):
        """Every technique ID in CRITERION_TO_PDF_TECHNIQUES exists in PDF_TECHNIQUES."""
        for criterion, tech_ids in CRITERION_TO_PDF_TECHNIQUES.items():
            for tid in tech_ids:
                assert tid in PDF_TECHNIQUES, (
                    f"CRITERION_TO_PDF_TECHNIQUES[{criterion}] references '{tid}' "
                    f"which does not exist in PDF_TECHNIQUES"
                )

    def test_forward_criterion_in_technique_wcag_criteria(self):
        """Each criterion in the map appears in the referenced technique's wcag_criteria."""
        for criterion, tech_ids in CRITERION_TO_PDF_TECHNIQUES.items():
            for tid in tech_ids:
                tech = PDF_TECHNIQUES[tid]
                assert criterion in tech.wcag_criteria, (
                    f"CRITERION_TO_PDF_TECHNIQUES says {criterion} -> {tid}, "
                    f"but {tid}.wcag_criteria = {tech.wcag_criteria} does not include '{criterion}'"
                )

    def test_reverse_technique_criteria_in_cross_reference(self):
        """Every criterion in a technique's wcag_criteria appears in CRITERION_TO_PDF_TECHNIQUES."""
        for tid, tech in PDF_TECHNIQUES.items():
            for criterion in tech.wcag_criteria:
                if criterion not in CRITERION_TO_PDF_TECHNIQUES:
                    # AAA or out-of-scope criteria are acceptable omissions
                    continue
                assert tid in CRITERION_TO_PDF_TECHNIQUES[criterion], (
                    f"{tid} claims criterion {criterion}, but "
                    f"CRITERION_TO_PDF_TECHNIQUES[{criterion}] = "
                    f"{CRITERION_TO_PDF_TECHNIQUES[criterion]} does not include '{tid}'"
                )

    def test_criterion_to_pdf_map_not_empty(self):
        """CRITERION_TO_PDF_TECHNIQUES has at least one entry."""
        assert len(CRITERION_TO_PDF_TECHNIQUES) > 0


class TestCriterionToFailureCrossReference:
    """Verify bi-directional consistency of CRITERION_TO_FAILURE_TECHNIQUES."""

    def test_forward_all_referenced_failures_exist(self):
        """Every failure ID in CRITERION_TO_FAILURE_TECHNIQUES exists in FAILURE_TECHNIQUES."""
        for criterion, fail_ids in CRITERION_TO_FAILURE_TECHNIQUES.items():
            for fid in fail_ids:
                assert fid in FAILURE_TECHNIQUES, (
                    f"CRITERION_TO_FAILURE_TECHNIQUES[{criterion}] references '{fid}' "
                    f"which does not exist in FAILURE_TECHNIQUES"
                )

    def test_forward_criterion_in_failure_wcag_criteria(self):
        """Each criterion in the map appears in the referenced failure's wcag_criteria."""
        for criterion, fail_ids in CRITERION_TO_FAILURE_TECHNIQUES.items():
            for fid in fail_ids:
                fail = FAILURE_TECHNIQUES[fid]
                assert criterion in fail.wcag_criteria, (
                    f"CRITERION_TO_FAILURE_TECHNIQUES says {criterion} -> {fid}, "
                    f"but {fid}.wcag_criteria = {fail.wcag_criteria} does not include '{criterion}'"
                )

    def test_criterion_to_failure_map_has_expected_criteria(self):
        """Known criteria with failure techniques are present in the map."""
        expected_criteria = {"1.1.1", "1.3.1", "2.4.2", "4.1.2"}
        for c in expected_criteria:
            assert c in CRITERION_TO_FAILURE_TECHNIQUES, (
                f"Expected criterion {c} in CRITERION_TO_FAILURE_TECHNIQUES"
            )


class TestRulesTechniqueCrossReference:
    """Verify WCAG_RULES_LEDGER technique fields match the authoritative cross-reference maps."""

    def test_rules_pdf_techniques_match_cross_reference(self):
        """Every rule's pdf_techniques matches CRITERION_TO_PDF_TECHNIQUES."""
        for rule in WCAG_RULES_LEDGER:
            expected = sorted(CRITERION_TO_PDF_TECHNIQUES.get(rule.criterion, []))
            actual = sorted(rule.pdf_techniques)
            assert actual == expected, (
                f"Rule {rule.criterion} pdf_techniques mismatch: "
                f"got {actual}, expected {expected}"
            )

    def test_rules_failure_techniques_match_cross_reference(self):
        """Every rule's failure_techniques matches CRITERION_TO_FAILURE_TECHNIQUES."""
        for rule in WCAG_RULES_LEDGER:
            expected = sorted(
                CRITERION_TO_FAILURE_TECHNIQUES.get(rule.criterion, [])
            )
            actual = sorted(rule.failure_techniques)
            assert actual == expected, (
                f"Rule {rule.criterion} failure_techniques mismatch: "
                f"got {actual}, expected {expected}"
            )

    def test_specific_rule_1_1_1_pdf_techniques(self):
        """Rule 1.1.1 has exactly PDF1 and PDF4."""
        rule = next(r for r in WCAG_RULES_LEDGER if r.criterion == "1.1.1")
        assert sorted(rule.pdf_techniques) == ["PDF1", "PDF4"]

    def test_specific_rule_1_1_1_failure_techniques(self):
        """Rule 1.1.1 has exactly F30, F38, F39, F65."""
        rule = next(r for r in WCAG_RULES_LEDGER if r.criterion == "1.1.1")
        assert sorted(rule.failure_techniques) == ["F30", "F38", "F39", "F65"]

    def test_specific_rule_1_3_1_pdf_techniques(self):
        """Rule 1.3.1 references all 8 structural techniques."""
        rule = next(r for r in WCAG_RULES_LEDGER if r.criterion == "1.3.1")
        expected = sorted(["PDF6", "PDF9", "PDF10", "PDF11", "PDF12",
                           "PDF17", "PDF20", "PDF21"])
        assert sorted(rule.pdf_techniques) == expected

    def test_specific_rule_3_1_1_pdf_techniques(self):
        """Rule 3.1.1 references PDF16 and PDF19."""
        rule = next(r for r in WCAG_RULES_LEDGER if r.criterion == "3.1.1")
        assert sorted(rule.pdf_techniques) == ["PDF16", "PDF19"]

    def test_rules_without_techniques_have_empty_lists(self):
        """Rules for criteria not in the cross-reference maps have empty lists."""
        criteria_with_pdf_techniques = set(CRITERION_TO_PDF_TECHNIQUES.keys())
        for rule in WCAG_RULES_LEDGER:
            if rule.criterion not in criteria_with_pdf_techniques:
                assert rule.pdf_techniques == [], (
                    f"Rule {rule.criterion} has unexpected pdf_techniques: "
                    f"{rule.pdf_techniques}"
                )


# ===========================================================================
# SECTION 3: Checker Dispatch Completeness Tests
# ===========================================================================


class TestCheckerDispatch:
    """Verify CHECK_DISPATCH is complete and consistent with WCAG_RULES_LEDGER."""

    def test_dispatch_table_has_50_entries(self):
        """CHECK_DISPATCH has exactly 50 entries — one per WCAG 2.1 AA criterion."""
        assert len(CHECK_DISPATCH) == 50

    def test_every_rule_has_check_function(self):
        """Every rule in the ledger has a corresponding check function in CHECK_DISPATCH."""
        for rule in WCAG_RULES_LEDGER:
            assert rule.check_fn_name in CHECK_DISPATCH, (
                f"Rule {rule.criterion} references check function "
                f"'{rule.check_fn_name}' but it is not in CHECK_DISPATCH"
            )

    def test_all_check_functions_callable(self):
        """Every entry in CHECK_DISPATCH is callable."""
        for name, fn in CHECK_DISPATCH.items():
            assert callable(fn), f"CHECK_DISPATCH['{name}'] is not callable"

    def test_no_orphaned_dispatch_entries(self):
        """Every entry in CHECK_DISPATCH is referenced by at least one rule."""
        referenced_fn_names = {rule.check_fn_name for rule in WCAG_RULES_LEDGER}
        for fn_name in CHECK_DISPATCH:
            assert fn_name in referenced_fn_names, (
                f"CHECK_DISPATCH has entry '{fn_name}' that is not "
                f"referenced by any rule in WCAG_RULES_LEDGER"
            )

    def test_dispatch_keys_follow_naming_convention(self):
        """All dispatch keys follow the check_N_N_N_description naming pattern."""
        import re
        pattern = re.compile(r"^check_\d+_\d+(_\d+)?_\w+$")
        for name in CHECK_DISPATCH:
            assert pattern.match(name), (
                f"CHECK_DISPATCH key '{name}' does not match "
                f"expected pattern 'check_N_N_N_description'"
            )


# ===========================================================================
# SECTION 4: Audit Execution Tests
# ===========================================================================


class TestRunFullAuditMinimalDoc:
    """Audit execution against a document with no content."""

    def test_full_audit_checks_all_50_rules(self):
        """run_full_audit checks exactly 50 rules against the minimal doc."""
        result = run_full_audit(_make_minimal_doc())
        assert result.rules_checked == 50

    def test_full_audit_minimal_doc_no_errors(self):
        """Minimal doc produces no ERROR findings — all check functions execute cleanly."""
        result = run_full_audit(_make_minimal_doc())
        assert result.rules_errored == 0

    def test_full_audit_minimal_doc_produces_findings(self):
        """Audit always produces at least one finding per rule."""
        result = run_full_audit(_make_minimal_doc())
        assert len(result.findings) >= 50

    def test_full_audit_minimal_doc_only_structural_fails(self):
        """Minimal doc produces no ERROR findings and only expected FAILs.

        Expected FAILs:
        - 2.4.5: bookmarks require heading structure (no headings in minimal doc)
        - 1.4.10, 1.4.11, 1.4.12, 2.1.1: cannot be verified automatically
          (these correctly FAIL with MANUAL_REVIEW instead of false PASS)
        All other rules should be PASS or NOT_APPLICABLE.

        Note: 1.4.3 (contrast), 2.4.3 (focus order), and 2.4.7 (focus visible)
        return NOT_APPLICABLE because contrast cannot be determined at IR stage
        and the minimal doc has no interactive elements.
        """
        result = run_full_audit(_make_minimal_doc())
        # Rules that are expected to FAIL on any document
        expected_fail_criteria = {
            "2.4.5",   # No headings → no bookmarks
            "1.4.10",  # Reflow cannot be automated
            "1.4.11",  # Non-text contrast cannot be automated
            "1.4.12",  # Text spacing cannot be automated
            "2.1.1",   # Keyboard cannot be automated
            "1.3.4",   # Orientation cannot be automated
            "3.1.2",   # Language of parts cannot be automated
        }
        unexpected_fails = [
            f for f in result.findings
            if f.status == FindingStatus.FAIL and f.criterion not in expected_fail_criteria
        ]
        assert unexpected_fails == [], (
            f"Unexpected FAIL findings beyond expected set: "
            f"{[(f.criterion, f.description) for f in unexpected_fails]}"
        )


class TestRunFullAuditRichDoc:
    """Audit execution against a document with diverse content."""

    def test_full_audit_checks_all_50_rules(self):
        """run_full_audit checks exactly 50 rules against the rich doc."""
        result = run_full_audit(_make_rich_doc())
        assert result.rules_checked == 50

    def test_full_audit_rich_doc_no_errors(self):
        """Rich doc produces no ERROR findings — all check functions execute cleanly."""
        result = run_full_audit(_make_rich_doc())
        assert result.rules_errored == 0

    def test_full_audit_rich_doc_finds_missing_alt(self):
        """Rich doc with empty alt on page 2 image produces at least one FAIL for 1.1.1."""
        result = run_full_audit(_make_rich_doc())
        alt_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_1_1_1" and f.status == FindingStatus.FAIL
        ]
        assert len(alt_findings) >= 1, (
            "Expected at least one FAIL for wcag_1_1_1 — page 2 has empty alt text"
        )

    def test_full_audit_rich_doc_missing_alt_references_page(self):
        """The 1.1.1 FAIL finding references the page with missing alt text."""
        result = run_full_audit(_make_rich_doc())
        alt_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_1_1_1" and f.status == FindingStatus.FAIL
        ]
        element_ids = [f.element_id for f in alt_findings]
        assert any("page_2" in eid for eid in element_ids), (
            f"Expected a page_2 element_id in 1.1.1 FAIL findings, got {element_ids}"
        )

    def test_full_audit_coverage_above_zero(self):
        """Coverage percentage is positive when the rich doc triggers checks."""
        result = run_full_audit(_make_rich_doc())
        assert result.coverage_pct > 0

    def test_full_audit_rich_doc_has_passes(self):
        """Rich doc has some PASS findings (headings, lists, language, title, etc.)."""
        result = run_full_audit(_make_rich_doc())
        assert result.rules_passed > 0

    def test_full_audit_counts_sum_to_rules_checked(self):
        """passed + failed + not_applicable + errored sums to rules_checked."""
        result = run_full_audit(_make_rich_doc())
        total = (
            result.rules_passed
            + result.rules_failed
            + result.rules_not_applicable
            + result.rules_errored
        )
        assert total == result.rules_checked, (
            f"Counts don't add up: {result.rules_passed}P + {result.rules_failed}F "
            f"+ {result.rules_not_applicable}NA + {result.rules_errored}E "
            f"= {total} != {result.rules_checked}"
        )


class TestFindingsToProposals:
    """Test findings_to_proposals conversion function."""

    def test_proposals_only_from_fail_findings(self):
        """findings_to_proposals only includes FAIL findings, not PASS or NOT_APPLICABLE."""
        result = run_full_audit(_make_rich_doc())
        proposals = findings_to_proposals(result.findings)
        fail_count = len([f for f in result.findings if f.status == FindingStatus.FAIL])
        assert len(proposals) == fail_count, (
            f"Expected {fail_count} proposals (one per FAIL finding), got {len(proposals)}"
        )

    def test_proposals_have_required_keys(self):
        """Each proposal dict has the required keys for the frontend."""
        result = run_full_audit(_make_rich_doc())
        proposals = findings_to_proposals(result.findings)
        required_keys = {
            "id", "category", "wcag_criterion", "element_type",
            "element_id", "description", "proposed_fix",
            "severity", "page", "auto_fixable", "action_type",
        }
        for i, proposal in enumerate(proposals):
            missing = required_keys - set(proposal.keys())
            assert not missing, (
                f"Proposal {i} is missing keys: {missing}"
            )

    def test_proposals_count_matches_fail_findings(self):
        """Proposals count equals the number of FAIL findings for any document."""
        # Use a doc that produces predictable FAILs
        doc = IRDocument(
            document_id="test-proposals-count",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.HEADING,
                            content="Introduction",
                            page_num=1,
                            attributes={"level": 1},
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        proposals = findings_to_proposals(result.findings)
        fail_count = len([f for f in result.findings if f.status == FindingStatus.FAIL])
        assert len(proposals) == fail_count

    def test_proposals_wcag_criterion_populated(self):
        """Each proposal's wcag_criterion is a non-empty string."""
        result = run_full_audit(_make_rich_doc())
        proposals = findings_to_proposals(result.findings)
        for proposal in proposals:
            assert proposal["wcag_criterion"], (
                f"Proposal has empty wcag_criterion: {proposal}"
            )

    def test_proposals_severity_is_valid_string(self):
        """Each proposal's severity is one of the valid FindingSeverity values."""
        valid_severities = {"critical", "serious", "moderate", "minor"}
        result = run_full_audit(_make_rich_doc())
        proposals = findings_to_proposals(result.findings)
        for proposal in proposals:
            assert proposal["severity"] in valid_severities, (
                f"Proposal has invalid severity: {proposal['severity']}"
            )


class TestAuditSummaryDict:
    """Test audit_summary_dict structure and values."""

    def test_audit_summary_has_all_expected_keys(self):
        """audit_summary_dict returns exactly the expected set of keys."""
        result = run_full_audit(_make_minimal_doc())
        summary = audit_summary_dict(result)
        expected_keys = {
            "total_issues", "critical", "serious", "moderate", "warning",
            "auto_fixable", "needs_review", "rules_checked", "rules_passed",
            "rules_failed", "rules_not_applicable", "rules_errored",
            "coverage_pct", "rules_breakdown",
        }
        assert set(summary.keys()) == expected_keys, (
            f"Summary key mismatch. Extra: {set(summary.keys()) - expected_keys}. "
            f"Missing: {expected_keys - set(summary.keys())}"
        )

    def test_audit_summary_titled_headings_doc_only_manual_review_issues(self):
        """A clean doc's only FAIL findings are manual-review stubs (cannot be automated)."""
        doc = IRDocument(
            document_id="test-clean",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.HEADING,
                            content="Introduction",
                            page_num=1,
                            attributes={"level": 1},
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Clean Document", "language": "en"},
        )
        result = run_full_audit(doc)
        # All FAIL findings on a clean doc should be manual-review stubs.
        # Note: 1.4.3 (contrast), 2.4.3 (focus order), and 2.4.7 (focus visible)
        # now return NOT_APPLICABLE because contrast cannot be determined at IR stage
        # and the clean doc has no interactive elements for focus checks.
        manual_review_criteria = {
            "1.3.4", "1.4.10", "1.4.11", "1.4.12",
            "2.1.1", "3.1.2",
        }
        non_stub_fails = [
            f for f in result.findings
            if f.status == FindingStatus.FAIL and f.criterion not in manual_review_criteria
        ]
        assert non_stub_fails == [], (
            f"Expected only manual-review stubs to FAIL on a clean doc, but got: "
            f"{[(f.criterion, f.description) for f in non_stub_fails]}"
        )

    def test_audit_summary_rules_checked_is_50(self):
        """Summary always reports rules_checked = 50."""
        for doc in [_make_minimal_doc(), _make_rich_doc()]:
            result = run_full_audit(doc)
            summary = audit_summary_dict(result)
            assert summary["rules_checked"] == 50

    def test_audit_summary_rich_doc_has_issues(self):
        """Rich doc summary reports at least one total_issue (empty alt text)."""
        result = run_full_audit(_make_rich_doc())
        summary = audit_summary_dict(result)
        assert summary["total_issues"] >= 1

    def test_audit_summary_severity_counts_sum_to_total_issues(self):
        """critical + serious + moderate + warning sums to total_issues."""
        result = run_full_audit(_make_rich_doc())
        summary = audit_summary_dict(result)
        severity_sum = (
            summary["critical"]
            + summary["serious"]
            + summary["moderate"]
            + summary["warning"]
        )
        assert severity_sum == summary["total_issues"], (
            f"Severity counts {severity_sum} != total_issues {summary['total_issues']}"
        )

    def test_audit_summary_auto_fixable_plus_needs_review_equals_total_issues(self):
        """auto_fixable + needs_review sums to total_issues."""
        result = run_full_audit(_make_rich_doc())
        summary = audit_summary_dict(result)
        assert (
            summary["auto_fixable"] + summary["needs_review"]
            == summary["total_issues"]
        ), (
            f"auto_fixable {summary['auto_fixable']} + needs_review "
            f"{summary['needs_review']} != total_issues {summary['total_issues']}"
        )

    def test_audit_summary_coverage_pct_is_float(self):
        """coverage_pct is a float in range [0.0, 100.0]."""
        result = run_full_audit(_make_rich_doc())
        summary = audit_summary_dict(result)
        assert isinstance(summary["coverage_pct"], float)
        assert 0.0 <= summary["coverage_pct"] <= 100.0


# ===========================================================================
# SECTION 5: Technique Reference in Findings Tests
#
# These tests verify that FAIL and PASS findings include technique IDs
# (e.g. "PDF1", "PDF6") in their ``evidence`` fields.
#
# wcag_checker.py already embeds technique references into finding evidence
# via the _pass() and _fail() helper functions using format_technique_refs().
# The FAIL finding tests pass as-is.
#
# The PASS test for ``TestPassFindingsTechniqueRefs`` is xfail because
# check_3_1_2_language_of_parts() constructs a raw RuleFinding() without
# going through _pass(), so it does not include the "PDF19 | Satisfies: ..."
# suffix in its evidence string.
# ===========================================================================


class TestFailFindingsTechniqueRefs:
    """FAIL findings for rules with pdf_techniques should include technique IDs in evidence."""

    def test_fail_findings_include_technique_refs(self):
        """Every FAIL finding for a rule with pdf_techniques includes a technique ID in evidence."""
        result = run_full_audit(_make_rich_doc())
        rules_with_techniques = {
            r.criterion: r for r in WCAG_RULES_LEDGER if r.pdf_techniques
        }

        failures: list[str] = []
        for finding in result.findings:
            if finding.status != FindingStatus.FAIL:
                continue
            rule = rules_with_techniques.get(finding.criterion)
            if rule is None:
                continue
            has_tech_ref = any(tid in finding.evidence for tid in rule.pdf_techniques)
            if not has_tech_ref:
                failures.append(
                    f"FAIL finding for {finding.criterion} is missing technique refs in evidence. "
                    f"Expected one of {rule.pdf_techniques} in: '{finding.evidence}'"
                )

        assert not failures, "\n".join(failures)

    def test_1_1_1_fail_includes_pdf1(self):
        """1.1.1 FAIL finding includes 'PDF1' in evidence."""
        result = run_full_audit(_make_rich_doc())
        fail_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_1_1_1" and f.status == FindingStatus.FAIL
        ]
        assert len(fail_findings) >= 1
        assert any("PDF1" in f.evidence for f in fail_findings), (
            f"No 'PDF1' found in evidence fields: {[f.evidence for f in fail_findings]}"
        )

    def test_1_3_1_fail_includes_pdf6(self):
        """1.3.1 FAIL finding for a table without headers includes 'PDF6' in evidence."""
        doc = IRDocument(
            document_id="test-table-no-headers",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.TABLE,
                            content="Data",
                            page_num=1,
                            attributes={"headers": [], "rows": [["a", "b"]]},
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        fail_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_1_3_1" and f.status == FindingStatus.FAIL
        ]
        assert len(fail_findings) >= 1
        assert any("PDF6" in f.evidence for f in fail_findings), (
            f"No 'PDF6' found in evidence fields: {[f.evidence for f in fail_findings]}"
        )

    def test_3_1_1_fail_includes_pdf16(self):
        """3.1.1 FAIL finding for missing language includes 'PDF16' in evidence."""
        doc = IRDocument(
            document_id="test-no-lang",
            filename="test.pdf",
            page_count=1,
            pages=[IRPage(page_num=1, blocks=[])],
            language="",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        fail_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_3_1_1" and f.status == FindingStatus.FAIL
        ]
        assert len(fail_findings) >= 1
        assert any("PDF16" in f.evidence for f in fail_findings), (
            f"No 'PDF16' found in evidence fields: {[f.evidence for f in fail_findings]}"
        )


@pytest.mark.xfail(
    reason=(
        "wcag_checker.py does not yet embed technique IDs in PASS finding evidence. "
        "These tests document the required behavior after enrichment."
    ),
    strict=False,
)
class TestPassFindingsTechniqueRefs:
    """PASS findings for rules with pdf_techniques should reference technique IDs in evidence."""

    def test_pass_findings_include_technique_refs(self):
        """Every PASS finding for a rule with pdf_techniques includes a technique ID in evidence."""
        result = run_full_audit(_make_rich_doc())
        rules_with_techniques = {
            r.criterion: r for r in WCAG_RULES_LEDGER if r.pdf_techniques
        }

        failures: list[str] = []
        for finding in result.findings:
            if finding.status != FindingStatus.PASS:
                continue
            rule = rules_with_techniques.get(finding.criterion)
            if rule is None:
                continue
            has_tech_ref = any(tid in finding.evidence for tid in rule.pdf_techniques)
            if not has_tech_ref:
                failures.append(
                    f"PASS finding for {finding.criterion} is missing technique refs in evidence. "
                    f"Expected one of {rule.pdf_techniques} in: '{finding.evidence}'"
                )

        assert not failures, "\n".join(failures)


# ===========================================================================
# SECTION 6: Specific Enrichment / Behavioural Tests
# ===========================================================================


class TestPlaceholderAltDetection:
    """Verify that placeholder alt text patterns are detected as failures."""

    def test_empty_alt_detected(self):
        """Empty string alt text fails 1.1.1."""
        doc = IRDocument(
            document_id="test-empty-alt",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.IMAGE,
                            content="",
                            page_num=1,
                            attributes={"alt": "", "image_id": "img1"},
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        alt_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_1_1_1" and f.status == FindingStatus.FAIL
        ]
        assert len(alt_findings) >= 1

    def test_placeholder_word_alt_detected(self):
        """Generic placeholder alt text ('image') fails 1.1.1."""
        doc = IRDocument(
            document_id="test-placeholder-alt",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.IMAGE,
                            content="",
                            page_num=1,
                            attributes={"alt": "image", "image_id": "img1"},
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        alt_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_1_1_1" and f.status == FindingStatus.FAIL
        ]
        assert len(alt_findings) >= 1, (
            "Placeholder alt 'image' should be detected as a 1.1.1 failure (F30)"
        )

    def test_descriptive_alt_passes(self):
        """Descriptive alt text passes 1.1.1."""
        doc = IRDocument(
            document_id="test-good-alt",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.IMAGE,
                            content="",
                            page_num=1,
                            attributes={
                                "alt": "Map of Sacramento County showing all 19 supervisorial districts",
                                "image_id": "img1",
                            },
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        alt_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_1_1_1" and f.status == FindingStatus.FAIL
        ]
        assert len(alt_findings) == 0, (
            f"Descriptive alt text should not fail 1.1.1. Got: {alt_findings}"
        )

    def test_figure_word_alt_detected(self):
        """'figure' as alt text is treated as a placeholder and fails 1.1.1."""
        doc = IRDocument(
            document_id="test-figure-alt",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.IMAGE,
                            content="",
                            page_num=1,
                            attributes={"alt": "figure", "image_id": "img1"},
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        alt_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_1_1_1" and f.status == FindingStatus.FAIL
        ]
        assert len(alt_findings) >= 1, (
            "Alt text 'figure' should be detected as a placeholder (F30 pattern)"
        )


class TestMissingLanguageDetection:
    """Verify that missing or empty language tag triggers 3.1.1 FAIL."""

    def test_empty_language_fails_3_1_1(self):
        """Empty language string fails 3.1.1 with at least one FAIL finding."""
        doc = IRDocument(
            document_id="test-no-lang",
            filename="test.pdf",
            page_count=1,
            pages=[IRPage(page_num=1, blocks=[])],
            language="",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        lang_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_3_1_1" and f.status == FindingStatus.FAIL
        ]
        assert len(lang_findings) >= 1

    def test_valid_language_passes_3_1_1(self):
        """Valid BCP 47 language tag passes 3.1.1."""
        for lang in ["en", "en-US", "es", "fr-CA"]:
            doc = IRDocument(
                document_id=f"test-lang-{lang}",
                filename="test.pdf",
                page_count=1,
                pages=[IRPage(page_num=1, blocks=[])],
                language=lang,
                metadata={"title": "Test", "language": lang},
            )
            result = run_full_audit(doc)
            lang_findings = [
                f for f in result.findings
                if f.rule_id == "wcag_3_1_1" and f.status == FindingStatus.FAIL
            ]
            assert len(lang_findings) == 0, (
                f"Language '{lang}' should pass 3.1.1 but got FAILs: {lang_findings}"
            )

    def test_invalid_language_tag_fails_3_1_1(self):
        """Invalid BCP 47 tag (e.g. '123', 'not-valid!!') fails 3.1.1."""
        for lang in ["123", "not-valid!!", "x"]:
            doc = IRDocument(
                document_id=f"test-invalid-lang",
                filename="test.pdf",
                page_count=1,
                pages=[IRPage(page_num=1, blocks=[])],
                language=lang,
                metadata={"title": "Test"},
            )
            result = run_full_audit(doc)
            lang_findings = [
                f for f in result.findings
                if f.rule_id == "wcag_3_1_1" and f.status == FindingStatus.FAIL
            ]
            assert len(lang_findings) >= 1, (
                f"Invalid language tag '{lang}' should fail 3.1.1"
            )


class TestTableStructureDetection:
    """Verify table header and structure checks."""

    def test_table_without_headers_fails_1_3_1(self):
        """A table with an empty headers list fails 1.3.1 (PDF6 / F90 / F91)."""
        doc = IRDocument(
            document_id="test-no-headers",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.TABLE,
                            content="Data",
                            page_num=1,
                            attributes={
                                "headers": [],
                                "rows": [["a", "b"], ["c", "d"]],
                            },
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        table_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_1_3_1" and f.status == FindingStatus.FAIL
        ]
        assert len(table_findings) >= 1, (
            "Table without headers should fail 1.3.1 (PDF6 technique)"
        )

    def test_table_with_headers_passes_1_3_1(self):
        """A table with proper headers passes 1.3.1."""
        doc = IRDocument(
            document_id="test-with-headers",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.TABLE,
                            content="Data",
                            page_num=1,
                            attributes={
                                "headers": ["Name", "Value"],
                                "rows": [["Alpha", "1"], ["Beta", "2"]],
                                "caption": "Sample data table",
                            },
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Test", "language": "en"},
        )
        result = run_full_audit(doc)
        table_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_1_3_1" and f.status == FindingStatus.FAIL
        ]
        assert len(table_findings) == 0, (
            f"Table with valid headers should pass 1.3.1, got: {table_findings}"
        )


class TestLinkPurposeDetection:
    """Verify that generic link text is detected as a 2.4.4 failure."""

    def test_generic_click_here_link_fails_2_4_4(self):
        """'click here' combined with a URL fails 2.4.4 (PDF11/PDF13 techniques)."""
        doc = IRDocument(
            document_id="test-generic-link",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.PARAGRAPH,
                            content=(
                                "For more information, click here: "
                                "https://example.com"
                            ),
                            page_num=1,
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        link_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_2_4_4" and f.status == FindingStatus.FAIL
        ]
        assert len(link_findings) >= 1, (
            "Generic link text 'click here' with URL should fail 2.4.4"
        )

    def test_descriptive_link_context_passes_2_4_4(self):
        """Descriptive surrounding text with a URL passes 2.4.4."""
        doc = IRDocument(
            document_id="test-good-link",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.PARAGRAPH,
                            content=(
                                "View the Sacramento County Annual Report at "
                                "https://www.saccounty.gov/annual-report"
                            ),
                            page_num=1,
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        link_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_2_4_4" and f.status == FindingStatus.FAIL
        ]
        assert len(link_findings) == 0, (
            f"Descriptive link context should pass 2.4.4, got: {link_findings}"
        )

    def test_read_more_link_fails_2_4_4(self):
        """'read more' combined with a URL fails 2.4.4."""
        doc = IRDocument(
            document_id="test-read-more-link",
            filename="test.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.PARAGRAPH,
                            content=(
                                "Read more at https://example.com/reports"
                            ),
                            page_num=1,
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Test"},
        )
        result = run_full_audit(doc)
        link_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_2_4_4" and f.status == FindingStatus.FAIL
        ]
        assert len(link_findings) >= 1, (
            "Generic link text 'read more' with URL should fail 2.4.4"
        )


class TestDocumentTitleChecks:
    """Verify page title detection (2.4.2 / PDF18 / F25)."""

    def test_metadata_title_passes_2_4_2(self):
        """Document with a metadata title passes 2.4.2."""
        doc = IRDocument(
            document_id="test-titled",
            filename="report.pdf",
            page_count=1,
            pages=[IRPage(page_num=1, blocks=[])],
            language="en",
            metadata={"title": "Sacramento County Annual Report 2025"},
        )
        result = run_full_audit(doc)
        title_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_2_4_2" and f.status == FindingStatus.FAIL
        ]
        assert len(title_findings) == 0, (
            f"Document with metadata title should pass 2.4.2, got: {title_findings}"
        )

    def test_empty_metadata_falls_back_to_filename_stem_detected(self):
        """Document with no metadata title falls back to filename stem — detected as non-descriptive (F25)."""
        doc = IRDocument(
            document_id="test-filename-title",
            filename="annual-report-2025.pdf",
            page_count=1,
            pages=[IRPage(page_num=1, blocks=[])],
            language="en",
            metadata={},
        )
        result = run_full_audit(doc)
        # The checker falls back to filename_base "annual-report-2025" which is
        # a filename stem pattern (all-lowercase with hyphens) — should fail F25
        title_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_2_4_2" and f.status == FindingStatus.FAIL
        ]
        assert len(title_findings) >= 1, (
            f"Filename stem 'annual-report-2025' should fail 2.4.2 (F25), got: {title_findings}"
        )

    def test_empty_metadata_with_descriptive_filename_passes(self):
        """Document with no metadata title but a descriptive filename passes 2.4.2."""
        doc = IRDocument(
            document_id="test-filename-title-good",
            filename="Sacramento County Annual Report 2025.pdf",
            page_count=1,
            pages=[IRPage(page_num=1, blocks=[])],
            language="en",
            metadata={},
        )
        result = run_full_audit(doc)
        # "Sacramento County Annual Report 2025" has spaces and mixed case — not a filename stem
        title_findings = [
            f for f in result.findings
            if f.rule_id == "wcag_2_4_2" and f.status == FindingStatus.FAIL
        ]
        assert len(title_findings) == 0, (
            f"Descriptive filename should pass 2.4.2, got: {title_findings}"
        )

    def test_title_finding_always_produced(self):
        """2.4.2 always produces at least one finding (PASS or FAIL)."""
        doc = IRDocument(
            document_id="test-any-title",
            filename="test.pdf",
            page_count=1,
            pages=[IRPage(page_num=1, blocks=[])],
            language="en",
            metadata={"title": "Test Doc"},
        )
        result = run_full_audit(doc)
        title_findings = [
            f for f in result.findings if f.rule_id == "wcag_2_4_2"
        ]
        assert len(title_findings) >= 1


class TestNoContentDocument:
    """Edge cases: document with no blocks on any page."""

    def test_empty_pages_no_errors(self):
        """A document with pages but no blocks does not cause any ERROR findings."""
        doc = IRDocument(
            document_id="test-empty-pages",
            filename="empty.pdf",
            page_count=3,
            pages=[
                IRPage(page_num=1, blocks=[]),
                IRPage(page_num=2, blocks=[]),
                IRPage(page_num=3, blocks=[]),
            ],
            language="en",
            metadata={"title": "Empty Document"},
        )
        result = run_full_audit(doc)
        assert result.rules_errored == 0

    def test_no_images_no_alt_text_failure(self):
        """A document with no images does not fail on alt text check."""
        doc = IRDocument(
            document_id="test-no-images",
            filename="text-only.pdf",
            page_count=1,
            pages=[
                IRPage(
                    page_num=1,
                    blocks=[
                        IRBlock(
                            block_type=BlockType.PARAGRAPH,
                            content="Simple text paragraph.",
                            page_num=1,
                        ),
                    ],
                )
            ],
            language="en",
            metadata={"title": "Text Only"},
        )
        result = run_full_audit(doc)
        alt_failures = [
            f for f in result.findings
            if f.rule_id == "wcag_1_1_1" and f.status == FindingStatus.FAIL
        ]
        assert len(alt_failures) == 0, (
            "Document with no images should not have 1.1.1 FAIL findings"
        )
