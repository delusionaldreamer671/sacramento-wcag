"""Tests for services/extraction/parser.py.

Each test targets one observable behaviour of parse_extraction_json,
_classify_element, _assign_complexity, or supporting helpers.
All tests are self-contained and require no external services.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.common.models import ComplexityFlag, WCAGCriterion
from services.extraction.parser import (
    _assign_complexity,
    _classify_element,
    _nesting_depth,
    _table_nesting_depth,
    parse_extraction_json,
)


# ---------------------------------------------------------------------------
# parse_extraction_json — empty / error inputs
# ---------------------------------------------------------------------------


def test_parse_empty_json_returns_empty_list():
    """An empty json_data dict produces no findings."""
    result = parse_extraction_json(document_id="doc-001", json_data={})
    assert result == []


def test_parse_json_with_empty_elements_list_returns_empty_list():
    """A valid payload whose 'elements' list is empty produces no findings."""
    result = parse_extraction_json(
        document_id="doc-001",
        json_data={"elements": []},
    )
    assert result == []


def test_empty_document_id_raises_value_error():
    """A blank document_id must raise ValueError before any parsing occurs."""
    with pytest.raises(ValueError):
        parse_extraction_json(document_id="", json_data={"elements": []})


def test_whitespace_only_document_id_raises_value_error():
    """A whitespace-only document_id is semantically empty and must raise ValueError."""
    with pytest.raises(ValueError):
        parse_extraction_json(document_id="   ", json_data={"elements": []})


# ---------------------------------------------------------------------------
# parse_extraction_json — image elements
# ---------------------------------------------------------------------------


def test_parse_image_element_creates_alt_text_finding(
    adobe_extract_image_json: dict[str, Any],
):
    """A Figure element without alt text produces exactly one WCAGFinding."""
    findings = parse_extraction_json(
        document_id="doc-img-001", json_data=adobe_extract_image_json
    )
    assert len(findings) == 1
    assert findings[0].criterion == WCAGCriterion.ALT_TEXT


def test_image_finding_has_critical_severity(adobe_extract_image_json: dict[str, Any]):
    """Alt-text findings for images are always classified as critical severity."""
    findings = parse_extraction_json(
        document_id="doc-img-002", json_data=adobe_extract_image_json
    )
    assert findings[0].severity == "critical"


def test_image_without_alt_flagged_as_review(adobe_extract_image_json: dict[str, Any]):
    """An image that has no alt text in its attributes receives REVIEW complexity."""
    findings = parse_extraction_json(
        document_id="doc-img-003", json_data=adobe_extract_image_json
    )
    assert findings[0].complexity == ComplexityFlag.REVIEW


def test_image_with_existing_alt_text_still_creates_finding_with_simple_complexity(
    adobe_extract_image_with_alt_json: dict[str, Any],
):
    """An image that already has alt text in attributes still produces a finding
    (for review/verification) but with SIMPLE complexity since no AI draft is needed."""
    findings = parse_extraction_json(
        document_id="doc-img-004", json_data=adobe_extract_image_with_alt_json
    )
    # A finding is still created because images always warrant a WCAG 1.1.1 check.
    assert len(findings) == 1
    assert findings[0].complexity == ComplexityFlag.SIMPLE


def test_artifact_image_is_not_flagged(adobe_extract_artifact_json: dict[str, Any]):
    """Elements with role=Artifact are decorative and must not produce any finding."""
    findings = parse_extraction_json(
        document_id="doc-artifact-001", json_data=adobe_extract_artifact_json
    )
    assert findings == []


# ---------------------------------------------------------------------------
# parse_extraction_json — table elements
# ---------------------------------------------------------------------------


def test_parse_table_element_creates_info_relationships_finding(
    adobe_extract_table_json: dict[str, Any],
):
    """A Table element produces exactly one WCAGFinding mapped to criterion 1.3.1."""
    findings = parse_extraction_json(
        document_id="doc-tbl-001", json_data=adobe_extract_table_json
    )
    assert len(findings) == 1
    assert findings[0].criterion == WCAGCriterion.INFO_RELATIONSHIPS


def test_deeply_nested_table_flagged_as_manual(
    adobe_extract_nested_table_json: dict[str, Any],
):
    """A Table path containing more than 2 TABLE segments must receive MANUAL complexity."""
    findings = parse_extraction_json(
        document_id="doc-tbl-nested-001", json_data=adobe_extract_nested_table_json
    )
    assert len(findings) == 1
    assert findings[0].complexity == ComplexityFlag.MANUAL


def test_deeply_nested_table_has_critical_severity(
    adobe_extract_nested_table_json: dict[str, Any],
):
    """A MANUAL-complexity nested table produces a critical-severity finding."""
    findings = parse_extraction_json(
        document_id="doc-tbl-nested-002", json_data=adobe_extract_nested_table_json
    )
    assert findings[0].severity == "critical"


# ---------------------------------------------------------------------------
# parse_extraction_json — heading elements
# ---------------------------------------------------------------------------


def test_parse_heading_element_creates_headings_finding(
    adobe_extract_heading_json: dict[str, Any],
):
    """A heading element produces exactly one WCAGFinding mapped to criterion 2.4.6."""
    findings = parse_extraction_json(
        document_id="doc-head-001", json_data=adobe_extract_heading_json
    )
    assert len(findings) == 1
    assert findings[0].criterion == WCAGCriterion.HEADINGS_LABELS


def test_heading_with_text_has_simple_complexity(
    adobe_extract_heading_json: dict[str, Any],
):
    """A heading element that has text content receives SIMPLE complexity."""
    findings = parse_extraction_json(
        document_id="doc-head-002", json_data=adobe_extract_heading_json
    )
    assert findings[0].complexity == ComplexityFlag.SIMPLE


def test_heading_without_text_flagged_as_review():
    """A heading element with no text content receives REVIEW complexity."""
    json_data = {
        "elements": [
            {"Path": "//Document/H1", "Text": "", "attributes": {}, "Page": 1}
        ]
    }
    findings = parse_extraction_json(document_id="doc-head-empty-001", json_data=json_data)
    assert len(findings) == 1
    assert findings[0].complexity == ComplexityFlag.REVIEW


# ---------------------------------------------------------------------------
# parse_extraction_json — link elements
# ---------------------------------------------------------------------------


def test_parse_link_element_creates_link_purpose_finding(
    adobe_extract_link_json: dict[str, Any],
):
    """A Link element produces exactly one WCAGFinding mapped to criterion 2.4.4."""
    findings = parse_extraction_json(
        document_id="doc-link-001", json_data=adobe_extract_link_json
    )
    assert len(findings) == 1
    assert findings[0].criterion == WCAGCriterion.LINK_PURPOSE


def test_link_with_text_has_serious_severity(adobe_extract_link_json: dict[str, Any]):
    """Link findings are always classified as serious severity."""
    findings = parse_extraction_json(
        document_id="doc-link-002", json_data=adobe_extract_link_json
    )
    assert findings[0].severity == "serious"


def test_link_without_text_flagged_as_review():
    """A link element with no visible text is flagged REVIEW for AI or manual drafting."""
    json_data = {
        "elements": [
            {"Path": "//Document/Link", "Text": "", "attributes": {}, "Page": 1}
        ]
    }
    findings = parse_extraction_json(document_id="doc-link-empty-001", json_data=json_data)
    assert len(findings) == 1
    assert findings[0].complexity == ComplexityFlag.REVIEW


# ---------------------------------------------------------------------------
# parse_extraction_json — paragraph elements
# ---------------------------------------------------------------------------


def test_simple_paragraph_does_not_create_finding(
    adobe_extract_paragraph_json: dict[str, Any],
):
    """A paragraph with text content is not flagged — standard paragraphs need no remediation."""
    findings = parse_extraction_json(
        document_id="doc-para-001", json_data=adobe_extract_paragraph_json
    )
    assert findings == []


def test_empty_paragraph_creates_finding():
    """An empty paragraph may represent structural garbage and should produce a finding."""
    json_data = {
        "elements": [
            {"Path": "//Document/P", "Text": "", "attributes": {}, "Page": 1}
        ]
    }
    findings = parse_extraction_json(document_id="doc-para-empty-001", json_data=json_data)
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# parse_extraction_json — element count and mixed documents
# ---------------------------------------------------------------------------


def test_element_count_matches_number_of_flaggable_elements(
    adobe_extract_mixed_json: dict[str, Any],
):
    """The number of findings must equal the number of elements that require remediation.

    Mixed payload has: H1, P (text — skipped), Figure, Table, H2, Link = 5 flaggable.
    """
    findings = parse_extraction_json(
        document_id="doc-mixed-001", json_data=adobe_extract_mixed_json
    )
    # H1 → finding, P (has text) → skipped, Figure → finding, Table → finding,
    # H2 → finding, Link → finding = 5
    assert len(findings) == 5


def test_each_finding_has_a_document_id(adobe_extract_mixed_json: dict[str, Any]):
    """Every finding in the result set must carry the correct document_id."""
    doc_id = "doc-mixed-002"
    findings = parse_extraction_json(document_id=doc_id, json_data=adobe_extract_mixed_json)
    for finding in findings:
        assert finding.document_id == doc_id


def test_each_finding_has_a_non_empty_element_id(
    adobe_extract_mixed_json: dict[str, Any],
):
    """Every finding must have a non-empty element_id for traceability."""
    findings = parse_extraction_json(
        document_id="doc-mixed-003", json_data=adobe_extract_mixed_json
    )
    for finding in findings:
        assert finding.element_id, f"Finding {finding.id} has an empty element_id"


def test_each_finding_has_a_suggested_fix(adobe_extract_mixed_json: dict[str, Any]):
    """Every finding must have a non-empty suggested_fix to guide reviewers."""
    findings = parse_extraction_json(
        document_id="doc-mixed-004", json_data=adobe_extract_mixed_json
    )
    for finding in findings:
        assert finding.suggested_fix, f"Finding {finding.id} is missing a suggested_fix"


# ---------------------------------------------------------------------------
# _classify_element — unit-level path matching
# ---------------------------------------------------------------------------


def test_classify_figure_path_returns_image():
    """A path containing 'Figure' is classified as 'image'."""
    elem = {"Path": "//Document/Sect/Figure"}
    assert _classify_element(elem) == "image"


def test_classify_table_path_returns_table():
    """A path ending in 'Table' is classified as 'table'."""
    elem = {"Path": "//Document/Table"}
    assert _classify_element(elem) == "table"


def test_classify_h2_path_returns_heading():
    """A path containing H2 is classified as 'heading'."""
    elem = {"Path": "//Document/H2"}
    assert _classify_element(elem) == "heading"


def test_classify_link_path_returns_link():
    """A path containing 'Link' is classified as 'link'."""
    elem = {"Path": "//Document/Link"}
    assert _classify_element(elem) == "link"


def test_classify_unknown_path_returns_paragraph():
    """An unrecognised path falls back to 'paragraph'."""
    elem = {"Path": "//Document/P"}
    assert _classify_element(elem) == "paragraph"


# ---------------------------------------------------------------------------
# _assign_complexity — unit-level rules
# ---------------------------------------------------------------------------


def test_assign_complexity_image_no_alt_returns_review():
    """An image element without alt text in attributes returns REVIEW."""
    elem = {"Path": "//Document/Figure", "attributes": {}}
    result = _assign_complexity("image", elem)
    assert result == ComplexityFlag.REVIEW


def test_assign_complexity_image_with_alt_returns_simple():
    """An image element with a populated Alt attribute returns SIMPLE."""
    elem = {"Path": "//Document/Figure", "attributes": {"Alt": "Sacramento seal"}}
    result = _assign_complexity("image", elem)
    assert result == ComplexityFlag.SIMPLE


def test_assign_complexity_table_with_colspan_returns_review():
    """A table element with a ColSpan attribute (merged cells) returns REVIEW."""
    elem = {"Path": "//Document/Table", "attributes": {"ColSpan": "2"}}
    result = _assign_complexity("table", elem)
    assert result == ComplexityFlag.REVIEW


def test_assign_complexity_deeply_nested_table_returns_manual():
    """A path containing more than 2 TABLE tokens returns MANUAL."""
    elem = {"Path": "//Document/Table/TR/TD/Table/TR/TD/Table", "attributes": {}}
    result = _assign_complexity("table", elem)
    assert result == ComplexityFlag.MANUAL


def test_assign_complexity_heading_with_text_returns_simple():
    """A heading element with text content returns SIMPLE."""
    elem = {"Path": "//Document/H1", "Text": "Executive Summary", "attributes": {}}
    result = _assign_complexity("heading", elem)
    assert result == ComplexityFlag.SIMPLE


def test_assign_complexity_link_without_text_returns_review():
    """A link element with empty text returns REVIEW."""
    elem = {"Path": "//Document/Link", "Text": "", "attributes": {}}
    result = _assign_complexity("link", elem)
    assert result == ComplexityFlag.REVIEW


def test_assign_complexity_paragraph_returns_simple():
    """Standard paragraphs always return SIMPLE complexity."""
    elem = {"Path": "//Document/P", "Text": "Some text.", "attributes": {}}
    result = _assign_complexity("paragraph", elem)
    assert result == ComplexityFlag.SIMPLE


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


def test_nesting_depth_empty_path_returns_zero():
    """An empty path string has a nesting depth of zero."""
    assert _nesting_depth("") == 0


def test_nesting_depth_simple_path():
    """A path with one structural level above Document returns the expected depth."""
    # "//Document/H1" has 3 slashes → depth = 3 - 1 = 2
    assert _nesting_depth("//Document/H1") == 2


def test_table_nesting_depth_counts_table_tokens():
    """_table_nesting_depth counts occurrences of TABLE in the path."""
    path = "//Document/Table/TR/TD/Table/TR/TD/Table"
    assert _table_nesting_depth(path) == 3


def test_table_nesting_depth_single_table():
    """A simple flat table path contains exactly one TABLE token."""
    path = "//Document/Table"
    assert _table_nesting_depth(path) == 1
