"""Shared pytest fixtures for the Sacramento County WCAG Remediation Pipeline tests.

Provides reusable mock data for PDF documents, extraction results, HITL review
items, and Adobe Extract JSON payloads. All fixtures are dependency-free and
require no external services.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from services.common.models import (
    ComplexityFlag,
    DocumentStatus,
    ExtractionResult,
    HITLReviewItem,
    PDFDocument,
    RemediatedDocument,
    WCAGCriterion,
    WCAGFinding,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXED_TS = datetime(2026, 2, 23, 12, 0, 0, tzinfo=timezone.utc)
_DOC_ID = "doc-1234-abcd-5678"
_FINDING_ID = "finding-aaaa-bbbb-cccc"


# ---------------------------------------------------------------------------
# PDFDocument fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pdf_document() -> PDFDocument:
    """A minimal PDFDocument in QUEUED status."""
    return PDFDocument(
        id=_DOC_ID,
        filename="sacramento_annual_report_2025.pdf",
        gcs_input_path="gs://sac-wcag-input/sacramento_annual_report_2025.pdf",
        gcs_output_path=None,
        status=DocumentStatus.QUEUED,
        page_count=12,
        created_at=_FIXED_TS,
        updated_at=_FIXED_TS,
    )


@pytest.fixture
def mock_pdf_document_hitl(mock_pdf_document: PDFDocument) -> PDFDocument:
    """A PDFDocument that has reached HITL_REVIEW status."""
    return mock_pdf_document.model_copy(
        update={"status": DocumentStatus.HITL_REVIEW, "updated_at": _FIXED_TS}
    )


# ---------------------------------------------------------------------------
# ExtractionResult fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_extraction_result() -> ExtractionResult:
    """A realistic ExtractionResult after successful Adobe Extract API call."""
    return ExtractionResult(
        document_id=_DOC_ID,
        adobe_job_id="adobe-job-xyz-9999",
        extracted_json_path="gs://sac-wcag-extract/doc-1234-abcd-5678/extract.json",
        auto_tag_json_path="gs://sac-wcag-extract/doc-1234-abcd-5678/autotag.json",
        elements_count=42,
        images_count=5,
        tables_count=3,
    )


# ---------------------------------------------------------------------------
# WCAGFinding fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_alt_text_finding() -> WCAGFinding:
    """A critical WCAGFinding for a missing alt-text image."""
    return WCAGFinding(
        id=_FINDING_ID,
        document_id=_DOC_ID,
        element_id="//Document/Figure:4",
        criterion=WCAGCriterion.ALT_TEXT,
        severity="critical",
        description="Image element at //Document/Figure is missing descriptive alt text (WCAG 1.1.1 Level A).",
        suggested_fix="Add a concise, descriptive Alt attribute.",
        ai_draft=None,
        complexity=ComplexityFlag.REVIEW,
    )


@pytest.fixture
def mock_table_finding() -> WCAGFinding:
    """A serious WCAGFinding for a table missing header associations."""
    return WCAGFinding(
        id="finding-table-0001",
        document_id=_DOC_ID,
        element_id="//Document/Table:7",
        criterion=WCAGCriterion.INFO_RELATIONSHIPS,
        severity="serious",
        description="Table at //Document/Table requires header associations (WCAG 1.3.1 Level A).",
        suggested_fix="Apply /TH tags with Scope attributes.",
        ai_draft=None,
        complexity=ComplexityFlag.REVIEW,
    )


@pytest.fixture
def mock_heading_finding() -> WCAGFinding:
    """A moderate WCAGFinding for a heading hierarchy issue."""
    return WCAGFinding(
        id="finding-head-0001",
        document_id=_DOC_ID,
        element_id="//Document/H2:1",
        criterion=WCAGCriterion.HEADINGS_LABELS,
        severity="moderate",
        description="Heading element at //Document/H2 needs verified hierarchy (WCAG 2.4.6 Level AA).",
        suggested_fix="Verify correct heading level and descriptive label.",
        ai_draft=None,
        complexity=ComplexityFlag.SIMPLE,
    )


# ---------------------------------------------------------------------------
# HITLReviewItem fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_hitl_item_pending() -> HITLReviewItem:
    """A HITLReviewItem that has not yet been reviewed."""
    return HITLReviewItem(
        id="hitl-0001",
        document_id=_DOC_ID,
        finding_id=_FINDING_ID,
        element_type="image",
        original_content={"Path": "//Document/Figure", "FilePath": "images/chart_01.png"},
        ai_suggestion="Bar chart showing 2025 annual budget by department.",
        reviewer_decision=None,
        reviewer_edit=None,
        reviewed_at=None,
        reviewed_by=None,
    )


@pytest.fixture
def mock_hitl_item_approved(mock_hitl_item_pending: HITLReviewItem) -> HITLReviewItem:
    """A HITLReviewItem approved by the reviewer."""
    return mock_hitl_item_pending.model_copy(
        update={
            "reviewer_decision": "approve",
            "reviewed_at": _FIXED_TS,
            "reviewed_by": "reviewer@saccounty.gov",
        }
    )


@pytest.fixture
def mock_hitl_item_edited(mock_hitl_item_pending: HITLReviewItem) -> HITLReviewItem:
    """A HITLReviewItem where the reviewer provided an edited suggestion."""
    return mock_hitl_item_pending.model_copy(
        update={
            "reviewer_decision": "edit",
            "reviewer_edit": "Pie chart showing department budget shares for FY2025, with Public Safety at 38%.",
            "reviewed_at": _FIXED_TS,
            "reviewed_by": "reviewer@saccounty.gov",
        }
    )


@pytest.fixture
def mock_hitl_item_rejected(mock_hitl_item_pending: HITLReviewItem) -> HITLReviewItem:
    """A HITLReviewItem that the reviewer rejected for manual remediation."""
    return mock_hitl_item_pending.model_copy(
        update={
            "reviewer_decision": "reject",
            "reviewed_at": _FIXED_TS,
            "reviewed_by": "reviewer@saccounty.gov",
        }
    )


@pytest.fixture
def mock_hitl_item_table_manual() -> HITLReviewItem:
    """A HITLReviewItem for a deeply nested table requiring manual remediation."""
    return HITLReviewItem(
        id="hitl-table-manual-0001",
        document_id=_DOC_ID,
        finding_id="finding-table-manual-0001",
        element_type="table",
        original_content={
            "Path": "//Document/Table/TR/TD/Table/TR/TD/Table",
            "attributes": {},
        },
        ai_suggestion="[MANUAL REMEDIATION REQUIRED — nested table depth >2]",
        reviewer_decision=None,
        reviewed_at=None,
        reviewed_by=None,
    )


# ---------------------------------------------------------------------------
# Adobe Extract JSON payloads
# ---------------------------------------------------------------------------


@pytest.fixture
def adobe_extract_empty_json() -> dict[str, Any]:
    """Adobe Extract JSON with an empty elements list."""
    return {
        "extended_metadata": {"page_count": 1},
        "elements": [],
    }


@pytest.fixture
def adobe_extract_image_json() -> dict[str, Any]:
    """Adobe Extract JSON containing a single image (Figure) element without alt text."""
    return {
        "elements": [
            {
                "Path": "//Document/Figure",
                "filePaths": ["images/figure_01.png"],
                "attributes": {},
                "Page": 1,
            }
        ]
    }


@pytest.fixture
def adobe_extract_image_with_alt_json() -> dict[str, Any]:
    """Adobe Extract JSON containing a Figure element that already has alt text."""
    return {
        "elements": [
            {
                "Path": "//Document/Figure",
                "filePaths": ["images/figure_02.png"],
                "attributes": {"Alt": "Sacramento County seal"},
                "Page": 1,
            }
        ]
    }


@pytest.fixture
def adobe_extract_table_json() -> dict[str, Any]:
    """Adobe Extract JSON containing a simple table element."""
    return {
        "elements": [
            {
                "Path": "//Document/Table",
                "Text": "Department Budget Table",
                "attributes": {},
                "Page": 2,
            }
        ]
    }


@pytest.fixture
def adobe_extract_nested_table_json() -> dict[str, Any]:
    """Adobe Extract JSON with a deeply nested table (>2 levels) that must be flagged MANUAL."""
    return {
        "elements": [
            {
                "Path": "//Document/Table/TR/TD/Table/TR/TD/Table",
                "Text": "",
                "attributes": {},
                "Page": 3,
            }
        ]
    }


@pytest.fixture
def adobe_extract_heading_json() -> dict[str, Any]:
    """Adobe Extract JSON containing a heading element."""
    return {
        "elements": [
            {
                "Path": "//Document/H2",
                "Text": "Executive Summary",
                "attributes": {},
                "Page": 1,
            }
        ]
    }


@pytest.fixture
def adobe_extract_link_json() -> dict[str, Any]:
    """Adobe Extract JSON containing a link element."""
    return {
        "elements": [
            {
                "Path": "//Document/Link",
                "Text": "Sacramento County Official Website",
                "attributes": {},
                "Page": 1,
            }
        ]
    }


@pytest.fixture
def adobe_extract_paragraph_json() -> dict[str, Any]:
    """Adobe Extract JSON containing a standard paragraph element."""
    return {
        "elements": [
            {
                "Path": "//Document/P",
                "Text": "This report summarises county operations for fiscal year 2025.",
                "attributes": {},
                "Page": 1,
            }
        ]
    }


@pytest.fixture
def adobe_extract_mixed_json() -> dict[str, Any]:
    """Adobe Extract JSON with multiple element types for integration-style tests."""
    return {
        "elements": [
            {
                "Path": "//Document/H1",
                "Text": "Sacramento County Annual Report 2025",
                "attributes": {},
                "Page": 1,
            },
            {
                "Path": "//Document/P",
                "Text": "Introduction paragraph with substantive content.",
                "attributes": {},
                "Page": 1,
            },
            {
                "Path": "//Document/Figure",
                "filePaths": ["images/cover.png"],
                "attributes": {},
                "Page": 1,
            },
            {
                "Path": "//Document/Table",
                "Text": "Summary Statistics",
                "attributes": {},
                "Page": 2,
            },
            {
                "Path": "//Document/H2",
                "Text": "Budget Overview",
                "attributes": {},
                "Page": 2,
            },
            {
                "Path": "//Document/Link",
                "Text": "Full budget document",
                "attributes": {},
                "Page": 2,
            },
        ]
    }


@pytest.fixture
def adobe_extract_artifact_json() -> dict[str, Any]:
    """Adobe Extract JSON element marked as Artifact (decorative — must be skipped)."""
    return {
        "elements": [
            {
                "Path": "//Document/Figure",
                "attributes": {"role": "Artifact"},
                "Page": 1,
            }
        ]
    }
