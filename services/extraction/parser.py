"""Parse Adobe Extract / Auto-Tag JSON output into WCAGFinding objects.

The Adobe Extract API returns a JSON structure with an "elements" array.
Each element has a "Path" field describing its position in the document
structure (e.g., "//Document/Sect/H1", "//Document/Table/TR/TD") and
a "Text" or "FilePath" (for images/figures) field.

This parser:
  1. Iterates over elements
  2. Identifies element types: images, tables, headings, links, paragraphs
  3. Assigns WCAGCriterion based on type
  4. Assigns ComplexityFlag based on nesting depth and content
  5. Sets severity based on WCAG impact level
  6. Returns a list of WCAGFinding objects ready for downstream processing
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from services.common.models import ComplexityFlag, WCAGCriterion, WCAGFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Element-type detection patterns
# ---------------------------------------------------------------------------

# Matches heading paths: //Document/.../H, H1–H6, Title
_HEADING_PATH_RE = re.compile(
    r"//(?:[^/]+/)*(?:H[1-6]?|Title)(?:/|$)", re.IGNORECASE
)

# Matches figure/image paths: //Document/.../Figure
_FIGURE_PATH_RE = re.compile(r"//(?:[^/]+/)*Figure(?:/|$)", re.IGNORECASE)

# Matches table paths: //Document/.../Table
_TABLE_PATH_RE = re.compile(r"//(?:[^/]+/)*Table(?:/|$)", re.IGNORECASE)

# Matches link paths: //Document/.../Link
_LINK_PATH_RE = re.compile(r"//(?:[^/]+/)*(?:Link|Reference)(?:/|$)", re.IGNORECASE)

# Matches list paths
_LIST_PATH_RE = re.compile(r"//(?:[^/]+/)*(?:L|LI|LBody)(?:/|$)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Nesting depth helper
# ---------------------------------------------------------------------------


def _nesting_depth(path: str) -> int:
    """Return the number of structural levels in an Adobe element path."""
    if not path:
        return 0
    return path.count("/") - 1  # leading // counts as one delimiter


def _table_nesting_depth(path: str) -> int:
    """Return number of nested Table tags within a path (for nested tables)."""
    return path.upper().count("TABLE")


# ---------------------------------------------------------------------------
# Element classifier
# ---------------------------------------------------------------------------

ElementType = str  # "image", "table", "heading", "link", "list", "paragraph"


def _classify_element(element: dict[str, Any]) -> ElementType:
    """Determine the semantic type of an Adobe extracted element."""
    path: str = element.get("Path", "")

    if _FIGURE_PATH_RE.search(path):
        return "image"
    if _TABLE_PATH_RE.search(path):
        return "table"
    if _HEADING_PATH_RE.search(path):
        return "heading"
    if _LINK_PATH_RE.search(path):
        return "link"
    if _LIST_PATH_RE.search(path):
        return "list"
    return "paragraph"


# ---------------------------------------------------------------------------
# Complexity assignment
# ---------------------------------------------------------------------------


def _assign_complexity(
    element_type: ElementType, element: dict[str, Any]
) -> ComplexityFlag:
    """Assign a ComplexityFlag to an element.

    Rules:
      - image without alt text  -> REVIEW
      - table with >2 nesting levels -> MANUAL
      - table with merged cells (spans) -> REVIEW
      - heading without text -> REVIEW
      - link without text -> REVIEW
      - everything else -> SIMPLE
    """
    path: str = element.get("Path", "")
    text: str = element.get("Text", "") or ""
    attributes: dict[str, Any] = element.get("attributes", {}) or {}

    if element_type == "image":
        # Images need AI-generated alt text; always send to review
        alt_text = attributes.get("Alt") or element.get("Alt") or ""
        if not alt_text.strip():
            return ComplexityFlag.REVIEW
        return ComplexityFlag.SIMPLE

    if element_type == "table":
        nested_table_count = _table_nesting_depth(path)
        if nested_table_count > 2:
            return ComplexityFlag.MANUAL
        # Check for colspan/rowspan attributes indicating merged cells
        has_spans = (
            attributes.get("ColSpan") is not None
            or attributes.get("RowSpan") is not None
        )
        if has_spans:
            return ComplexityFlag.REVIEW
        return ComplexityFlag.REVIEW  # Tables always warrant review

    if element_type == "heading":
        if not text.strip():
            return ComplexityFlag.REVIEW
        return ComplexityFlag.SIMPLE

    if element_type == "link":
        if not text.strip():
            return ComplexityFlag.REVIEW
        return ComplexityFlag.SIMPLE

    # list, paragraph
    return ComplexityFlag.SIMPLE


# ---------------------------------------------------------------------------
# Severity assignment
# ---------------------------------------------------------------------------


def _assign_severity(element_type: ElementType, complexity: ComplexityFlag) -> str:
    """Assign WCAG severity level.

    severity levels: critical, serious, moderate, minor

    - Missing alt text on images: critical (WCAG 1.1.1 is Level A)
    - Nested tables without headers: critical
    - Table without headers: serious
    - Heading hierarchy issues: moderate
    - Link purpose issues: serious
    - Other: minor
    """
    if element_type == "image":
        return "critical"
    if element_type == "table":
        if complexity == ComplexityFlag.MANUAL:
            return "critical"
        return "serious"
    if element_type == "link":
        return "serious"
    if element_type == "heading":
        return "moderate"
    return "minor"


# ---------------------------------------------------------------------------
# WCAG criterion mapping
# ---------------------------------------------------------------------------

_CRITERION_MAP: dict[ElementType, WCAGCriterion] = {
    "image": WCAGCriterion.ALT_TEXT,           # 1.1.1 Non-text Content
    "table": WCAGCriterion.INFO_RELATIONSHIPS,  # 1.3.1 Info and Relationships
    "heading": WCAGCriterion.HEADINGS_LABELS,   # 2.4.6 Headings and Labels
    "link": WCAGCriterion.LINK_PURPOSE,         # 2.4.4 Link Purpose
    "list": WCAGCriterion.INFO_RELATIONSHIPS,   # 1.3.1
    "paragraph": WCAGCriterion.READING_ORDER,   # 1.3.2 Meaningful Sequence
}


def _get_criterion(element_type: ElementType) -> WCAGCriterion:
    return _CRITERION_MAP.get(element_type, WCAGCriterion.READING_ORDER)


# ---------------------------------------------------------------------------
# Description builder
# ---------------------------------------------------------------------------


def _build_description(
    element_type: ElementType,
    element: dict[str, Any],
    complexity: ComplexityFlag,
) -> str:
    """Generate a human-readable description of the WCAG issue."""
    path = element.get("Path", "(unknown path)")
    text_preview = str(element.get("Text", ""))[:80].strip()
    preview = f" (text: '{text_preview}')" if text_preview else ""

    if element_type == "image":
        return (
            f"Image element at {path} is missing descriptive alt text "
            f"(WCAG 1.1.1 Level A){preview}."
        )
    if element_type == "table":
        if complexity == ComplexityFlag.MANUAL:
            return (
                f"Deeply nested table (>2 levels) at {path} requires manual "
                f"remediation to establish proper header associations (WCAG 1.3.1 Level A)."
            )
        return (
            f"Table at {path} requires header associations and semantic "
            f"HTML structure (WCAG 1.3.1 Level A){preview}."
        )
    if element_type == "heading":
        return (
            f"Heading element at {path} needs verified hierarchy and "
            f"descriptive label (WCAG 2.4.6 Level AA){preview}."
        )
    if element_type == "link":
        return (
            f"Link element at {path} requires meaningful link text for "
            f"context (WCAG 2.4.4 Level A){preview}."
        )
    if element_type == "list":
        return (
            f"List element at {path} requires proper semantic list structure "
            f"(WCAG 1.3.1 Level A){preview}."
        )
    return (
        f"Content element at {path} must have correct reading order "
        f"in the tag tree (WCAG 1.3.2 Level A){preview}."
    )


# ---------------------------------------------------------------------------
# Suggested fix templates
# ---------------------------------------------------------------------------


def _build_suggested_fix(element_type: ElementType, element: dict[str, Any]) -> str:
    """Return a brief actionable suggestion for remediation."""
    if element_type == "image":
        return (
            "Add a concise, descriptive Alt attribute to the /Figure tag "
            "that conveys the meaning and purpose of the image."
        )
    if element_type == "table":
        return (
            "Identify column and row header cells, apply /TH tags with Scope "
            "attributes (Row/Column/Both), and ensure all /TD cells reference "
            "their headers via the Headers attribute."
        )
    if element_type == "heading":
        text = element.get("Text", "")
        return (
            f"Verify that '{text[:60]}' uses the correct heading level in the "
            f"document hierarchy and has a meaningful, descriptive label."
        )
    if element_type == "link":
        return (
            "Ensure link text is descriptive of the link's destination or purpose, "
            "not generic text like 'click here' or 'read more'."
        )
    if element_type == "list":
        return (
            "Wrap list items in a proper /L tag with /LI children containing "
            "/Lbl (bullet/number) and /LBody (content) sub-tags."
        )
    return (
        "Verify reading order matches the visual presentation by adjusting "
        "the tag tree sequence in the remediated PDF."
    )


# ---------------------------------------------------------------------------
# Should we create a finding for this element?
# ---------------------------------------------------------------------------


def _should_create_finding(element_type: ElementType, element: dict[str, Any]) -> bool:
    """Return True if this element type warrants a WCAGFinding.

    Paragraphs are only flagged if they contain unusual structure.
    Decorative elements (role=Artifact) are skipped.
    """
    # Skip artifacts (decorative/background elements)
    attributes: dict[str, Any] = element.get("attributes", {}) or {}
    if attributes.get("role") == "Artifact":
        return False

    # Skip page-level structural containers that don't map to WCAG issues
    path: str = element.get("Path", "")
    skip_patterns = [
        "//Document$",
        "//Sect$",
        "//Part$",
        "//Art$",
        "//Div$",
        "//Span$",
    ]
    for pattern in skip_patterns:
        if re.fullmatch(pattern.replace("$", ""), path.strip()):
            return False

    # Always flag images, tables, links
    if element_type in ("image", "table", "link"):
        return True

    # Flag headings
    if element_type == "heading":
        return True

    # Flag lists for structure review
    if element_type == "list":
        # Only flag the list root, not every LI
        if "//LI" in path or "//LBody" in path:
            return False
        return True

    # Paragraphs: only flag if they lack text (may be a misclassified element)
    if element_type == "paragraph":
        text = (element.get("Text") or "").strip()
        if not text:
            return True  # Empty paragraph may be structural garbage
        return False

    return False


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------


def parse_extraction_json(
    document_id: str,
    json_data: dict[str, Any],
) -> list[WCAGFinding]:
    """Parse Adobe Extract API JSON output into a list of WCAGFinding objects.

    Args:
        document_id: The UUID of the PDFDocument being processed.
        json_data: The parsed JSON response from the Adobe Extract API.
                   Expected to have an "elements" key with a list of elements.

    Returns:
        A list of WCAGFinding objects, one per accessibility issue identified.
        Returns an empty list if json_data is empty or has no elements.

    Raises:
        ValueError: if document_id is empty.
    """
    if not document_id or not document_id.strip():
        raise ValueError("document_id must not be empty")

    if not json_data:
        logger.warning("parse_extraction_json: received empty json_data for document %s", document_id)
        return []

    elements: list[dict[str, Any]] = json_data.get("elements", [])
    if not isinstance(elements, list):
        logger.error(
            "parse_extraction_json: 'elements' is not a list for document %s (got %s)",
            document_id,
            type(elements).__name__,
        )
        return []

    logger.info(
        "parse_extraction_json: processing %d raw elements for document %s",
        len(elements),
        document_id,
    )

    findings: list[WCAGFinding] = []
    skipped_count = 0
    type_counts: dict[str, int] = {}

    for idx, element in enumerate(elements):
        if not isinstance(element, dict):
            logger.debug("Skipping non-dict element at index %d", idx)
            skipped_count += 1
            continue

        element_type = _classify_element(element)
        type_counts[element_type] = type_counts.get(element_type, 0) + 1

        if not _should_create_finding(element_type, element):
            skipped_count += 1
            continue

        complexity = _assign_complexity(element_type, element)
        severity = _assign_severity(element_type, complexity)
        criterion = _get_criterion(element_type)
        description = _build_description(element_type, element, complexity)
        suggested_fix = _build_suggested_fix(element_type, element)

        # Build a stable element_id from path + index
        path = element.get("Path", "")
        element_id = f"{path}:{idx}" if path else str(uuid.uuid4())

        finding = WCAGFinding(
            document_id=document_id,
            element_id=element_id,
            criterion=criterion,
            severity=severity,
            description=description,
            suggested_fix=suggested_fix,
            complexity=complexity,
        )
        findings.append(finding)

    logger.info(
        "parse_extraction_json: document_id=%s elements_processed=%d "
        "findings_created=%d skipped=%d type_breakdown=%s",
        document_id,
        len(elements),
        len(findings),
        skipped_count,
        type_counts,
    )

    return findings
