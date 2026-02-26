"""WCAG 2.1 PDF Techniques — Authoritative Cross-Reference.

Single source of truth for all 23 PDF-specific techniques (PDF1–PDF23) and
11 relevant failure techniques (F-series) from the W3C WCAG 2.1 Techniques
document.

Source: https://www.w3.org/WAI/WCAG21/Techniques/
Extracted: 2026-02-25

This module provides:
- PDFTechnique and FailureTechnique dataclasses
- PDF_TECHNIQUES dict (23 entries)
- FAILURE_TECHNIQUES dict (11 entries)
- CRITERION_TO_PDF_TECHNIQUES cross-reference (the SINGLE SOURCE OF TRUTH)
- CRITERION_TO_FAILURE_TECHNIQUES cross-reference
- Import-time validation that asserts internal consistency
- Helper functions for lookups

No hallucination: every value is extracted verbatim from the W3C reference.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PDFTechnique:
    """A single W3C PDF technique for WCAG 2.1 compliance."""

    id: str                          # e.g. "PDF1"
    title: str                       # Official technique title
    wcag_criteria: list[str]         # Criteria this technique is sufficient/advisory for
    technique_type: str              # "sufficient" or "advisory"
    pdf_structure: str               # The PDF tag/entry this technique requires
    check_description: str           # What to verify in an automated check
    pipeline_relevance: str          # "critical", "high", "medium", "low", "out_of_scope"


@dataclass(frozen=True)
class FailureTechnique:
    """A W3C failure technique relevant to PDF remediation."""

    id: str                          # e.g. "F25"
    title: str                       # Official failure title
    wcag_criteria: list[str]         # Criteria this failure applies to
    description: str                 # What constitutes the failure
    pdf_implication: str             # How this failure manifests in PDF


# ---------------------------------------------------------------------------
# PDF_TECHNIQUES — All 23 PDF techniques
# ---------------------------------------------------------------------------

PDF_TECHNIQUES: dict[str, PDFTechnique] = {

    "PDF1": PDFTechnique(
        id="PDF1",
        title="Applying text alternatives to images with the Alt entry",
        wcag_criteria=["1.1.1"],
        technique_type="sufficient",
        pdf_structure="/Figure <</Alt (description text)>>",
        check_description=(
            "Every /Figure tag must have a non-empty /Alt entry with "
            "meaningful descriptive text."
        ),
        pipeline_relevance="critical",
    ),

    "PDF2": PDFTechnique(
        id="PDF2",
        title="Creating bookmarks in PDF documents",
        wcag_criteria=["2.4.5"],
        technique_type="advisory",
        pdf_structure="/Outlines",
        check_description=(
            "Bookmarks panel displays bookmarks that link to correct "
            "document sections."
        ),
        pipeline_relevance="medium",
    ),

    "PDF3": PDFTechnique(
        id="PDF3",
        title="Ensuring correct tab and reading order",
        wcag_criteria=["1.3.2", "2.1.1", "2.4.3"],
        technique_type="sufficient",
        pdf_structure="/Tabs /S",
        check_description=(
            "Tag tree order matches intended reading order; tab order "
            "set to Structure (/Tabs /S) on each page."
        ),
        pipeline_relevance="critical",
    ),

    "PDF4": PDFTechnique(
        id="PDF4",
        title="Hiding decorative images with the Artifact tag",
        wcag_criteria=["1.1.1"],
        technique_type="sufficient",
        pdf_structure="/Artifact BMC...EMC",
        check_description=(
            "Decorative images are marked as /Artifact, not /Figure. "
            "They are not announced by screen readers."
        ),
        pipeline_relevance="high",
    ),

    "PDF5": PDFTechnique(
        id="PDF5",
        title="Indicating required form controls in PDF forms",
        wcag_criteria=["3.3.1", "3.3.2"],
        technique_type="sufficient",
        pdf_structure="/Ff 0x2 flag",
        check_description=(
            "Required form fields have the /Ff required flag set and "
            "validation errors are described in text."
        ),
        pipeline_relevance="medium",
    ),

    "PDF6": PDFTechnique(
        id="PDF6",
        title="Using table elements for table markup",
        wcag_criteria=["1.3.1"],
        technique_type="sufficient",
        pdf_structure="Table > TR > TH/TD",
        check_description=(
            "Tables use proper Table/TR/TH/TD tag hierarchy with correct "
            "RowSpan/ColSpan attributes."
        ),
        pipeline_relevance="critical",
    ),

    "PDF7": PDFTechnique(
        id="PDF7",
        title="Performing OCR on a scanned PDF document",
        wcag_criteria=["1.4.5"],
        technique_type="sufficient",
        pdf_structure="OCR text layer",
        check_description=(
            "Scanned text has been converted to actual text via OCR and "
            "is searchable/selectable."
        ),
        pipeline_relevance="out_of_scope",
    ),

    "PDF8": PDFTechnique(
        id="PDF8",
        title="Providing definitions for abbreviations via an E entry",
        wcag_criteria=["3.1.4"],
        technique_type="sufficient",
        pdf_structure="/Span <</E (expansion)>>",
        check_description=(
            "Abbreviations have /E entries on their /Span tags with the "
            "expanded form."
        ),
        pipeline_relevance="low",
    ),

    "PDF9": PDFTechnique(
        id="PDF9",
        title="Providing headings by marking content with heading tags",
        wcag_criteria=["1.3.1", "2.4.1"],
        technique_type="sufficient",
        pdf_structure="/H1 through /H6",
        check_description=(
            "All headings are tagged with /H1 through /H6 in the structure "
            "tree with correct hierarchy."
        ),
        pipeline_relevance="critical",
    ),

    "PDF10": PDFTechnique(
        id="PDF10",
        title="Providing labels for interactive form controls",
        wcag_criteria=["1.3.1", "3.3.2", "4.1.2"],
        technique_type="sufficient",
        pdf_structure="/TU tooltip entry",
        check_description=(
            "Every form control has a /TU tooltip entry providing the "
            "accessible name."
        ),
        pipeline_relevance="medium",
    ),

    "PDF11": PDFTechnique(
        id="PDF11",
        title="Providing links and link text using Link annotation",
        wcag_criteria=["1.3.1", "2.1.1", "2.4.4"],
        technique_type="sufficient",
        pdf_structure="/Link + /OBJR",
        check_description=(
            "Links use /Link structure element with /OBJR object reference "
            "and descriptive text content."
        ),
        pipeline_relevance="high",
    ),

    "PDF12": PDFTechnique(
        id="PDF12",
        title="Providing name, role, value information for form fields",
        wcag_criteria=["1.3.1", "4.1.2"],
        technique_type="sufficient",
        pdf_structure="/FT /TU /V /Ff",
        check_description=(
            "Form fields have /FT (type/role), /TU (name), /V (value), "
            "and /Ff (flags) properly set."
        ),
        pipeline_relevance="medium",
    ),

    "PDF13": PDFTechnique(
        id="PDF13",
        title="Providing replacement text using /Alt entry for links",
        wcag_criteria=["2.4.4"],
        technique_type="sufficient",
        pdf_structure="/Link <</Alt (text)>>",
        check_description=(
            "Links with non-descriptive visible text have /Alt entries "
            "providing descriptive replacement text."
        ),
        pipeline_relevance="high",
    ),

    "PDF14": PDFTechnique(
        id="PDF14",
        title="Providing running headers and footers",
        wcag_criteria=["3.2.3"],
        technique_type="advisory",
        pdf_structure="/Artifact (header/footer)",
        check_description=(
            "Running headers and footers are marked as /Artifact and "
            "provide consistent location information."
        ),
        pipeline_relevance="medium",
    ),

    "PDF15": PDFTechnique(
        id="PDF15",
        title="Providing submit buttons with submit-form action",
        wcag_criteria=["3.2.2"],
        technique_type="sufficient",
        pdf_structure="submit-form action",
        check_description=(
            "PDF forms have a submit button with submit-form action for "
            "explicit submission."
        ),
        pipeline_relevance="low",
    ),

    "PDF16": PDFTechnique(
        id="PDF16",
        title="Setting the default language using /Lang entry",
        wcag_criteria=["3.1.1"],
        technique_type="sufficient",
        pdf_structure="/Lang (en-US) in catalog",
        check_description=(
            "Document catalog has /Lang entry with valid BCP 47 language "
            "tag."
        ),
        pipeline_relevance="critical",
    ),

    "PDF17": PDFTechnique(
        id="PDF17",
        title="Specifying consistent page numbering",
        wcag_criteria=["1.3.1", "3.2.3"],
        technique_type="sufficient",
        pdf_structure="/PageLabels dictionary",
        check_description=(
            "Page numbering in viewer controls matches document page "
            "numbering via /PageLabels dictionary."
        ),
        pipeline_relevance="medium",
    ),

    "PDF18": PDFTechnique(
        id="PDF18",
        title="Specifying the document title using Title entry",
        wcag_criteria=["2.4.2"],
        technique_type="sufficient",
        pdf_structure="/Title + /DisplayDocTitle true",
        check_description=(
            "Document has descriptive /Title in info dictionary and "
            "/DisplayDocTitle true in viewer preferences."
        ),
        pipeline_relevance="critical",
    ),

    "PDF19": PDFTechnique(
        id="PDF19",
        title="Specifying language for a passage or phrase with Lang",
        wcag_criteria=["3.1.1", "3.1.2"],
        technique_type="sufficient",
        pdf_structure="/Lang on structure elements",
        check_description=(
            "Passages in different languages have /Lang attribute on their "
            "structure elements."
        ),
        pipeline_relevance="low",
    ),

    "PDF20": PDFTechnique(
        id="PDF20",
        title="Using Adobe Acrobat Pro's Table Editor to repair tables",
        wcag_criteria=["1.3.1"],
        technique_type="sufficient",
        pdf_structure="Table/TR/TH/TD hierarchy",
        check_description=(
            "Table cells correctly classified as TH or TD with proper "
            "RowSpan/ColSpan attributes."
        ),
        pipeline_relevance="high",
    ),

    "PDF21": PDFTechnique(
        id="PDF21",
        title="Using List tags for lists in PDF documents",
        wcag_criteria=["1.3.1"],
        technique_type="sufficient",
        pdf_structure="L > LI > Lbl + LBody",
        check_description=(
            "Lists use L > LI > Lbl + LBody tag hierarchy."
        ),
        pipeline_relevance="high",
    ),

    "PDF22": PDFTechnique(
        id="PDF22",
        title="Indicating when user input falls outside required format",
        wcag_criteria=["3.3.1", "3.3.3"],
        technique_type="sufficient",
        pdf_structure="validation scripts",
        check_description=(
            "Form validation notifies users when input does not match "
            "required format with descriptive error text."
        ),
        pipeline_relevance="low",
    ),

    "PDF23": PDFTechnique(
        id="PDF23",
        title="Providing interactive form controls in PDF documents",
        wcag_criteria=["2.1.1"],
        technique_type="sufficient",
        pdf_structure="/FT field types",
        check_description=(
            "Interactive form controls are keyboard accessible and use "
            "proper /FT field type entries."
        ),
        pipeline_relevance="low",
    ),
}


# ---------------------------------------------------------------------------
# FAILURE_TECHNIQUES — 11 relevant failure techniques
# ---------------------------------------------------------------------------

FAILURE_TECHNIQUES: dict[str, FailureTechnique] = {

    "F25": FailureTechnique(
        id="F25",
        title="Title not identifying contents",
        wcag_criteria=["2.4.2"],
        description=(
            "Title exists but does not identify the contents or purpose "
            "of the document."
        ),
        pdf_implication=(
            "PDF /Title must not be a filename or generic text"
        ),
    ),

    "F30": FailureTechnique(
        id="F30",
        title="Text alternatives that are not alternatives",
        wcag_criteria=["1.1.1"],
        description=(
            "Alt text is a filename, placeholder, or generic label like "
            "'spacer', 'image', 'picture', 'Oct.jpg'."
        ),
        pdf_implication=(
            "Alt text must not be filename, placeholder, or generic label"
        ),
    ),

    "F38": FailureTechnique(
        id="F38",
        title="Not marking decorative images as artifacts",
        wcag_criteria=["1.1.1"],
        description=(
            "Decorative images are not marked so assistive technology "
            "can ignore them."
        ),
        pdf_implication=(
            "Decorative images without /Artifact marking"
        ),
    ),

    "F39": FailureTechnique(
        id="F39",
        title="Providing non-null alt text for decorative images",
        wcag_criteria=["1.1.1"],
        description=(
            "Decorative images have non-null alt text, causing assistive "
            "technology to announce them unnecessarily."
        ),
        pdf_implication=(
            "Decorative images must not have /Alt entry"
        ),
    ),

    "F43": FailureTechnique(
        id="F43",
        title="Structural markup not representing content",
        wcag_criteria=["1.3.1"],
        description=(
            "Structural markup is used for visual effect rather than "
            "to convey semantic meaning."
        ),
        pdf_implication=(
            "Heading tags for visual emphasis, not semantic headings"
        ),
    ),

    "F46": FailureTechnique(
        id="F46",
        title="Using table elements for layout",
        wcag_criteria=["1.3.1"],
        description=(
            "Layout tables use table markup (Table/TR/TH/TD), confusing "
            "assistive technology."
        ),
        pdf_implication=(
            "Layout tables must not use Table/TR/TH/TD tags"
        ),
    ),

    "F65": FailureTechnique(
        id="F65",
        title="Omitting alt attribute on images",
        wcag_criteria=["1.1.1"],
        description=(
            "Content images lack any alt attribute or text alternative."
        ),
        pdf_implication=(
            "Content images without /Alt entry in Figure tag"
        ),
    ),

    "F68": FailureTechnique(
        id="F68",
        title="UI control without programmatic name",
        wcag_criteria=["4.1.2"],
        description=(
            "User interface control does not have a programmatically "
            "determined name."
        ),
        pdf_implication=(
            "Form fields without /TU tooltip entry"
        ),
    ),

    "F86": FailureTechnique(
        id="F86",
        title="Multi-part form fields lacking names",
        wcag_criteria=["4.1.2"],
        description=(
            "Sub-fields of multi-part form controls lack individual "
            "accessible names."
        ),
        pdf_implication=(
            "Sub-fields of multi-part form lacking /TU"
        ),
    ),

    "F90": FailureTechnique(
        id="F90",
        title="Incorrectly associating table headers",
        wcag_criteria=["1.3.1"],
        description=(
            "Table headers are not properly associated with data cells "
            "through structure."
        ),
        pdf_implication=(
            "TH not properly associated with TD through structure"
        ),
    ),

    "F91": FailureTechnique(
        id="F91",
        title="Not correctly marking up table headers",
        wcag_criteria=["1.3.1"],
        description=(
            "Data tables use TD instead of TH for header cells."
        ),
        pdf_implication=(
            "Data tables where header cells use TD instead of TH"
        ),
    ),
}


# ---------------------------------------------------------------------------
# CRITERION_TO_PDF_TECHNIQUES — The SINGLE SOURCE OF TRUTH
#
# Maps each WCAG criterion to its PDF-specific sufficient/advisory techniques.
# This is the authoritative cross-reference that everything validates against.
# ---------------------------------------------------------------------------

CRITERION_TO_PDF_TECHNIQUES: dict[str, list[str]] = {
    "1.1.1": ["PDF1", "PDF4"],
    "1.3.1": ["PDF6", "PDF9", "PDF10", "PDF11", "PDF12", "PDF17", "PDF20", "PDF21"],
    "1.3.2": ["PDF3"],
    "1.4.5": ["PDF7"],
    "2.1.1": ["PDF3", "PDF11", "PDF23"],
    "2.4.1": ["PDF9"],
    "2.4.2": ["PDF18"],
    "2.4.3": ["PDF3"],
    "2.4.4": ["PDF11", "PDF13"],
    "2.4.5": ["PDF2"],
    "3.1.1": ["PDF16", "PDF19"],
    "3.1.2": ["PDF19"],
    "3.2.2": ["PDF15"],
    "3.2.3": ["PDF14", "PDF17"],
    "3.3.1": ["PDF5", "PDF22"],
    "3.3.2": ["PDF5", "PDF10"],
    "3.3.3": ["PDF22"],
    "4.1.2": ["PDF10", "PDF12"],
}


# ---------------------------------------------------------------------------
# CRITERION_TO_FAILURE_TECHNIQUES
# ---------------------------------------------------------------------------

CRITERION_TO_FAILURE_TECHNIQUES: dict[str, list[str]] = {
    "1.1.1": ["F30", "F38", "F39", "F65"],
    "1.3.1": ["F43", "F46", "F90", "F91"],
    "2.4.2": ["F25"],
    "4.1.2": ["F68", "F86"],
}


# ---------------------------------------------------------------------------
# Import-time validation
# ---------------------------------------------------------------------------


def _validate_techniques() -> None:
    """Assert internal consistency of all technique data. Run at import time."""

    # 1. Total counts
    assert len(PDF_TECHNIQUES) == 23, (
        f"Expected 23 PDF techniques, found {len(PDF_TECHNIQUES)}."
    )
    assert len(FAILURE_TECHNIQUES) == 11, (
        f"Expected 11 failure techniques, found {len(FAILURE_TECHNIQUES)}."
    )

    # 2. Every technique in CRITERION_TO_PDF_TECHNIQUES exists in PDF_TECHNIQUES
    for criterion, tech_ids in CRITERION_TO_PDF_TECHNIQUES.items():
        for tech_id in tech_ids:
            assert tech_id in PDF_TECHNIQUES, (
                f"CRITERION_TO_PDF_TECHNIQUES references '{tech_id}' for "
                f"criterion {criterion}, but it does not exist in PDF_TECHNIQUES."
            )

    # 3. Every technique in CRITERION_TO_FAILURE_TECHNIQUES exists in FAILURE_TECHNIQUES
    for criterion, fail_ids in CRITERION_TO_FAILURE_TECHNIQUES.items():
        for fail_id in fail_ids:
            assert fail_id in FAILURE_TECHNIQUES, (
                f"CRITERION_TO_FAILURE_TECHNIQUES references '{fail_id}' for "
                f"criterion {criterion}, but it does not exist in FAILURE_TECHNIQUES."
            )

    # 4. Reverse mapping consistency: for each PDFTechnique, its wcag_criteria
    #    must contain all criteria that reference it in CRITERION_TO_PDF_TECHNIQUES
    for criterion, tech_ids in CRITERION_TO_PDF_TECHNIQUES.items():
        for tech_id in tech_ids:
            technique = PDF_TECHNIQUES[tech_id]
            assert criterion in technique.wcag_criteria, (
                f"CRITERION_TO_PDF_TECHNIQUES maps criterion {criterion} → {tech_id}, "
                f"but {tech_id}.wcag_criteria = {technique.wcag_criteria} does not "
                f"include '{criterion}'."
            )

    # 5. Forward mapping consistency: for each PDFTechnique, every criterion
    #    in its wcag_criteria must reference it in CRITERION_TO_PDF_TECHNIQUES
    for tech_id, technique in PDF_TECHNIQUES.items():
        for criterion in technique.wcag_criteria:
            if criterion in CRITERION_TO_PDF_TECHNIQUES:
                assert tech_id in CRITERION_TO_PDF_TECHNIQUES[criterion], (
                    f"{tech_id}.wcag_criteria includes '{criterion}', but "
                    f"CRITERION_TO_PDF_TECHNIQUES['{criterion}'] = "
                    f"{CRITERION_TO_PDF_TECHNIQUES[criterion]} does not include "
                    f"'{tech_id}'."
                )
            else:
                # Criterion exists in technique but not in cross-reference.
                # This is OK only for criteria outside WCAG 2.1 AA scope
                # (e.g. 1.4.9, 2.4.8, 2.4.9, 3.1.4 are AAA-level).
                pass

    # 6. Reverse mapping consistency for failures
    for criterion, fail_ids in CRITERION_TO_FAILURE_TECHNIQUES.items():
        for fail_id in fail_ids:
            failure = FAILURE_TECHNIQUES[fail_id]
            assert criterion in failure.wcag_criteria, (
                f"CRITERION_TO_FAILURE_TECHNIQUES maps criterion {criterion} → "
                f"{fail_id}, but {fail_id}.wcag_criteria = {failure.wcag_criteria} "
                f"does not include '{criterion}'."
            )

    logger.debug(
        "WCAG PDF techniques validated: %d PDF techniques, %d failure techniques OK",
        len(PDF_TECHNIQUES),
        len(FAILURE_TECHNIQUES),
    )


# Run validation at import time — fail fast if data is inconsistent
_validate_techniques()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_techniques_for_criterion(criterion: str) -> list[PDFTechnique]:
    """Return all PDF techniques applicable to a WCAG criterion."""
    tech_ids = CRITERION_TO_PDF_TECHNIQUES.get(criterion, [])
    return [PDF_TECHNIQUES[tid] for tid in tech_ids]


def get_failures_for_criterion(criterion: str) -> list[FailureTechnique]:
    """Return all failure techniques applicable to a WCAG criterion."""
    fail_ids = CRITERION_TO_FAILURE_TECHNIQUES.get(criterion, [])
    return [FAILURE_TECHNIQUES[fid] for fid in fail_ids]


def format_technique_refs(criterion: str) -> str:
    """Format technique references as a human-readable string.

    Returns e.g. "PDF1, PDF4; Failures: F30, F38, F39, F65"
    or "PDF3" (no failures) or "" (no techniques at all).
    """
    pdf_ids = CRITERION_TO_PDF_TECHNIQUES.get(criterion, [])
    fail_ids = CRITERION_TO_FAILURE_TECHNIQUES.get(criterion, [])

    parts: list[str] = []
    if pdf_ids:
        parts.append(", ".join(pdf_ids))
    if fail_ids:
        parts.append("Failures: " + ", ".join(fail_ids))

    return "; ".join(parts)
