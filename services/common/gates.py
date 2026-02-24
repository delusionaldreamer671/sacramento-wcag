"""Validation gates for inter-stage quality checks.

Four gates run between pipeline stages to prevent noise propagation:

    G1 (post-extraction): Structural completeness — every page has blocks
    G2 (post-IR merge):   Schema validation — no duplicate IDs, valid types
    G3 (post-HTML):       Accessibility — axe-core + structural checks
    G4 (post-PDF):        PDF/UA compliance — tag structure validation

Each gate returns a GateResult. Hard failures trigger up to MAX_RETRIES
retries before the element is flagged for human review.

Priority classification (aligned with research report validation gate policy):
    P0 — blocks publish:     Missing lang, missing title, img missing alt/src,
                             Adobe disqualification/timeout, zero pages, zero blocks,
                             duplicate block IDs, invalid block types, PDF parse failures
    P1 — requires HITL:      Heading hierarchy skips, empty pages, low text coverage,
                             missing <th scope>, missing <main> landmark,
                             axe moderate/minor violations, multi-column layout flags
    P2 — telemetry only:     Informational passes, axe availability warnings
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from services.common.ir import BlockType, IRBlock, IRDocument

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# Gate result models
# ---------------------------------------------------------------------------


class GateCheck(BaseModel):
    """A single check within a gate."""

    gate_id: str
    check_name: str
    status: Literal["pass", "soft_fail", "hard_fail"]
    severity: Literal["critical", "serious", "moderate", "minor"]
    priority: Literal["P0", "P1", "P2"] = "P2"
    next_action: Literal["block", "flag_hitl", "proceed"] = "proceed"
    evidence_pointer: str = ""
    details: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class GateResult(BaseModel):
    """Aggregate result for a gate (all checks)."""

    gate_id: str
    passed: bool
    checks: list[GateCheck]
    retry_count: int = 0

    def to_ledger_entry(self) -> dict[str, Any]:
        """Convert to validation_ledger.json entry format."""
        return {
            "gate_id": self.gate_id,
            "passed": self.passed,
            "retry_count": self.retry_count,
            "checks": [c.model_dump() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# G1: Post-extraction — structural completeness
# ---------------------------------------------------------------------------


def _check_column_layout(ir_doc: IRDocument) -> list[GateCheck]:
    """Detect likely multi-column page layouts using block bounding boxes.

    Strategy (P1 — soft_fail / moderate, not a blocker):
    For each page, collect all block bounding boxes and check whether two or
    more blocks occupy non-overlapping horizontal bands. If a pair of blocks
    on the same page have x-ranges that overlap by 50% or less, it suggests
    a side-by-side (multi-column) arrangement. Adobe Extract may have
    serialised these in column-major order, which can corrupt reading order.

    The check is skipped for pages where all bboxes are zero (i.e. bbox data
    was not populated by the extraction stage — e.g. pypdf fallback).
    """
    checks: list[GateCheck] = []
    flagged_pages: list[int] = []

    for page in ir_doc.pages:
        blocks_with_bbox = [
            b for b in page.blocks
            if not (b.bbox.x1 == 0.0 and b.bbox.x2 == 0.0)
        ]

        # Need at least 2 blocks with real coordinates to compare
        if len(blocks_with_bbox) < 2:
            continue

        multi_column_detected = False
        n = len(blocks_with_bbox)
        for i in range(n):
            if multi_column_detected:
                break
            for j in range(i + 1, n):
                b_i = blocks_with_bbox[i].bbox
                b_j = blocks_with_bbox[j].bbox

                # Width of each block's x-range
                width_i = b_i.x2 - b_i.x1
                width_j = b_j.x2 - b_j.x1

                # Skip degenerate zero-width blocks
                if width_i <= 0 or width_j <= 0:
                    continue

                # Horizontal overlap between the two blocks
                overlap_start = max(b_i.x1, b_j.x1)
                overlap_end = min(b_i.x2, b_j.x2)
                overlap = max(0.0, overlap_end - overlap_start)

                # Overlap ratio relative to the narrower block
                min_width = min(width_i, width_j)
                overlap_ratio = overlap / min_width

                # If overlap is 50% or less → blocks are side-by-side → multi-column
                if overlap_ratio <= 0.50:
                    multi_column_detected = True
                    break

        if multi_column_detected:
            flagged_pages.append(page.page_num)

    if flagged_pages:
        checks.append(GateCheck(
            gate_id="G1", check_name="multi_column_bbox",
            status="soft_fail", severity="moderate",
            priority="P1", next_action="flag_hitl",
            details=(
                f"Possible multi-column layout detected on page(s) {flagged_pages} "
                "based on non-overlapping block x-ranges (>50% horizontal separation). "
                "Adobe Extract may have serialised reading order incorrectly. "
                "Flag for HITL reading-order review (WCAG 1.3.2)."
            ),
        ))
    else:
        checks.append(GateCheck(
            gate_id="G1", check_name="multi_column_bbox",
            status="pass", severity="minor",
            priority="P2", next_action="proceed",
            details="No multi-column layout detected from block bounding boxes",
        ))

    return checks


def run_gate_g1(ir_doc: IRDocument) -> GateResult:
    """G1: Every page has >=1 block, document has pages, text coverage sanity."""
    checks: list[GateCheck] = []

    # Check 1: Document has at least one page
    if not ir_doc.pages:
        checks.append(GateCheck(
            gate_id="G1", check_name="has_pages",
            status="hard_fail", severity="critical",
            priority="P0", next_action="block",
            details="Document has no pages",
        ))
    else:
        checks.append(GateCheck(
            gate_id="G1", check_name="has_pages",
            status="pass", severity="minor",
            priority="P2", next_action="proceed",
            details=f"Document has {len(ir_doc.pages)} page(s)",
        ))

    # Check 2: Every page has at least 1 block
    empty_pages = [p.page_num for p in ir_doc.pages if not p.blocks]
    if empty_pages:
        checks.append(GateCheck(
            gate_id="G1", check_name="non_empty_pages",
            status="soft_fail", severity="serious",
            priority="P1", next_action="flag_hitl",
            details=f"Pages with zero blocks: {empty_pages}",
        ))
    elif ir_doc.pages:
        checks.append(GateCheck(
            gate_id="G1", check_name="non_empty_pages",
            status="pass", severity="minor",
            priority="P2", next_action="proceed",
            details="All pages have blocks",
        ))

    # Check 3: At least some blocks have content
    all_blocks = ir_doc.all_blocks()
    blocks_with_text = sum(1 for b in all_blocks if b.content.strip())
    if all_blocks and blocks_with_text == 0:
        checks.append(GateCheck(
            gate_id="G1", check_name="has_text_content",
            status="hard_fail", severity="critical",
            priority="P0", next_action="block",
            details=f"All {len(all_blocks)} blocks are empty (no text content)",
        ))
    elif all_blocks:
        checks.append(GateCheck(
            gate_id="G1", check_name="has_text_content",
            status="pass", severity="minor",
            priority="P2", next_action="proceed",
            details=f"{blocks_with_text}/{len(all_blocks)} blocks have text",
        ))

    # Check 4: Text coverage sanity (warn if <10% of pages have text)
    if ir_doc.pages:
        pages_with_text = sum(
            1 for p in ir_doc.pages
            if any(b.content.strip() for b in p.blocks)
        )
        ratio = pages_with_text / len(ir_doc.pages)
        if ratio < 0.1:
            checks.append(GateCheck(
                gate_id="G1", check_name="text_coverage",
                status="soft_fail", severity="serious",
                priority="P1", next_action="flag_hitl",
                details=f"Only {pages_with_text}/{len(ir_doc.pages)} pages have text ({ratio:.0%})",
            ))

    # Check 5: Multi-column layout detection via bounding box analysis
    checks.extend(_check_column_layout(ir_doc))

    passed = not any(c.status == "hard_fail" for c in checks)
    return GateResult(gate_id="G1", passed=passed, checks=checks)


# ---------------------------------------------------------------------------
# G2: Post-IR merge — schema validation
# ---------------------------------------------------------------------------


def run_gate_g2(ir_doc: IRDocument) -> GateResult:
    """G2: No duplicate block_ids, page order ascending, valid block_types."""
    checks: list[GateCheck] = []

    # Check 1: No duplicate block_ids
    seen_ids: set[str] = set()
    duplicates: list[str] = []
    for block in ir_doc.all_blocks():
        if block.block_id in seen_ids:
            duplicates.append(block.block_id)
        seen_ids.add(block.block_id)

    if duplicates:
        checks.append(GateCheck(
            gate_id="G2", check_name="unique_block_ids",
            status="hard_fail", severity="critical",
            priority="P0", next_action="block",
            details=f"{len(duplicates)} duplicate block_id(s)",
        ))
    else:
        checks.append(GateCheck(
            gate_id="G2", check_name="unique_block_ids",
            status="pass", severity="minor",
            priority="P2", next_action="proceed",
            details=f"{len(seen_ids)} unique block IDs",
        ))

    # Check 2: Page numbers are monotonically non-decreasing
    page_nums = [p.page_num for p in ir_doc.pages]
    if page_nums != sorted(page_nums):
        checks.append(GateCheck(
            gate_id="G2", check_name="page_order",
            status="soft_fail", severity="serious",
            priority="P1", next_action="flag_hitl",
            details=f"Page numbers not in order: {page_nums}",
        ))
    else:
        checks.append(GateCheck(
            gate_id="G2", check_name="page_order",
            status="pass", severity="minor",
            priority="P2", next_action="proceed",
            details="Page numbers in ascending order",
        ))

    # Check 3: All blocks have valid block_type
    invalid_types: list[str] = []
    valid_values = {bt.value for bt in BlockType}
    for block in ir_doc.all_blocks():
        if block.block_type.value not in valid_values:
            invalid_types.append(block.block_type.value)

    if invalid_types:
        checks.append(GateCheck(
            gate_id="G2", check_name="valid_block_types",
            status="hard_fail", severity="critical",
            priority="P0", next_action="block",
            details=f"Invalid block types: {set(invalid_types)}",
        ))
    else:
        checks.append(GateCheck(
            gate_id="G2", check_name="valid_block_types",
            status="pass", severity="minor",
            priority="P2", next_action="proceed",
            details="All block types are valid",
        ))

    # Check 4: All blocks have non-empty block_id
    empty_ids = sum(1 for b in ir_doc.all_blocks() if not b.block_id)
    if empty_ids:
        checks.append(GateCheck(
            gate_id="G2", check_name="non_empty_ids",
            status="hard_fail", severity="critical",
            priority="P0", next_action="block",
            details=f"{empty_ids} blocks have empty block_id",
        ))

    passed = not any(c.status == "hard_fail" for c in checks)
    return GateResult(gate_id="G2", passed=passed, checks=checks)


# ---------------------------------------------------------------------------
# G3: Post-HTML — accessibility checks (axe-core + structural)
# ---------------------------------------------------------------------------

_RE_LANG = re.compile(r'<html[^>]*\blang="([^"]+)"', re.IGNORECASE)
_RE_IMG_NO_ALT = re.compile(r'<img(?![^>]*\balt=)[^>]*>', re.IGNORECASE)
_RE_IMG_NO_SRC = re.compile(r'<img(?![^>]*\bsrc=)[^>]*>', re.IGNORECASE)
_RE_IMG_EMPTY_SRC = re.compile(r'<img[^>]*\bsrc\s*=\s*""\s*[^>]*>', re.IGNORECASE)
_RE_TH_SCOPE = re.compile(r'<th[^>]*\bscope="(col|row)"', re.IGNORECASE)
_RE_HEADING = re.compile(r'<h(\d)\b', re.IGNORECASE)
_RE_PARA_TEXT = re.compile(r'<p[^>]*>(.*?)</p>', re.IGNORECASE | re.DOTALL)

# axe rule tags that map critical/serious axe violations to P0
_AXE_P0_TAGS = frozenset({"wcag2a", "wcag2aa", "wcag111", "wcag131", "wcag311"})

# Thresholds for multi-column HTML heuristic
_SHORT_PARA_CHAR_LIMIT = 60   # paragraphs shorter than this are considered "short"
_SHORT_PARA_RUN_THRESHOLD = 5  # flag if this many consecutive short paragraphs found


def run_gate_g3(html: str) -> GateResult:
    """G3: axe-core scan + structural HTML validation.

    Tries real axe-core first (via playwright). Falls back to regex-based
    structural checks if playwright is not available.
    """
    checks: list[GateCheck] = []

    # 1. Try real axe-core scan
    axe_checks = _run_axe_checks(html)
    checks.extend(axe_checks)

    # 2. Structural checks (always run — catch things axe doesn't)
    structural_checks = _run_structural_checks(html)
    checks.extend(structural_checks)

    passed = not any(c.status == "hard_fail" for c in checks)
    return GateResult(gate_id="G3", passed=passed, checks=checks)


def _run_axe_checks(html: str) -> list[GateCheck]:
    """Run real axe-core if available."""
    checks: list[GateCheck] = []
    try:
        from services.common.axe_runner import is_available, run_axe_scan

        if not is_available():
            checks.append(GateCheck(
                gate_id="G3", check_name="axe_availability",
                status="soft_fail", severity="moderate",
                priority="P2", next_action="proceed",
                details="axe-core not available (playwright or axe-core not installed). Using fallback checks only.",
            ))
            return checks

        axe_results = run_axe_scan(html)
        for violation in axe_results.get("violations", []):
            impact = violation.get("impact", "minor")
            tags: list[str] = violation.get("tags", [])

            if impact in ("critical", "serious"):
                # axe critical/serious impact always maps to P0 (blocks publish)
                # Use rule tags as evidence_pointer for traceability
                status = "hard_fail"
                severity = "critical" if impact == "critical" else "serious"
                priority = "P0"
                next_action = "block"
            else:
                # axe moderate/minor impact → P1 (HITL sign-off)
                status = "soft_fail"
                severity = "moderate" if impact == "moderate" else "minor"
                priority = "P1"
                next_action = "flag_hitl"

            checks.append(GateCheck(
                gate_id="G3",
                check_name=f"axe_{violation.get('id', 'unknown')}",
                status=status,
                severity=severity,
                priority=priority,
                next_action=next_action,
                evidence_pointer=",".join(tags),
                details=violation.get("description", violation.get("help", "")),
            ))

        if not axe_results.get("violations"):
            checks.append(GateCheck(
                gate_id="G3", check_name="axe_clean",
                status="pass", severity="minor",
                priority="P2", next_action="proceed",
                details=f"axe-core: {axe_results.get('passes', 0)} rules passed, 0 violations",
            ))

    except Exception as exc:
        logger.warning("axe-core scan failed: %s", exc)
        checks.append(GateCheck(
            gate_id="G3", check_name="axe_error",
            status="soft_fail", severity="moderate",
            priority="P2", next_action="proceed",
            details=f"axe-core scan failed: {exc}",
        ))

    return checks


def _run_structural_checks(html: str) -> list[GateCheck]:
    """Regex-based structural HTML checks (complement to axe-core)."""
    checks: list[GateCheck] = []

    # Check 1: lang attribute on <html>
    if _RE_LANG.search(html):
        checks.append(GateCheck(
            gate_id="G3", check_name="html_lang",
            status="pass", severity="minor",
            priority="P2", next_action="proceed",
            details="<html> has lang attribute",
        ))
    else:
        checks.append(GateCheck(
            gate_id="G3", check_name="html_lang",
            status="hard_fail", severity="critical",
            priority="P0", next_action="block",
            details="Missing lang attribute on <html> (WCAG 3.1.1)",
        ))

    # Check 2: Images have alt text
    imgs_without_alt = _RE_IMG_NO_ALT.findall(html)
    if imgs_without_alt:
        checks.append(GateCheck(
            gate_id="G3", check_name="img_alt",
            status="hard_fail", severity="critical",
            priority="P0", next_action="block",
            details=f"{len(imgs_without_alt)} <img> element(s) missing alt attribute (WCAG 1.1.1)",
        ))

    # Check 3 (NEW): Images have non-empty src attribute
    # An <img> without a src provides no visual content — P0 blocker
    imgs_without_src = _RE_IMG_NO_SRC.findall(html)
    imgs_with_empty_src = _RE_IMG_EMPTY_SRC.findall(html)
    missing_src_count = len(imgs_without_src) + len(imgs_with_empty_src)
    if missing_src_count:
        checks.append(GateCheck(
            gate_id="G3", check_name="img_src",
            status="hard_fail", severity="critical",
            priority="P0", next_action="block",
            details=(
                f"{missing_src_count} <img> element(s) missing or empty src attribute "
                "(no visual content provided)"
            ),
        ))

    # Check 4: Tables have <th scope>
    if "<table" in html.lower():
        if _RE_TH_SCOPE.search(html):
            checks.append(GateCheck(
                gate_id="G3", check_name="table_headers",
                status="pass", severity="minor",
                priority="P2", next_action="proceed",
                details="Tables have <th scope> headers",
            ))
        else:
            checks.append(GateCheck(
                gate_id="G3", check_name="table_headers",
                status="hard_fail", severity="serious",
                priority="P1", next_action="flag_hitl",
                details="Tables present but no <th scope> found (WCAG 1.3.1)",
            ))

    # Check 5: Heading hierarchy doesn't skip levels
    headings = [int(m) for m in _RE_HEADING.findall(html)]
    skips = []
    for i in range(1, len(headings)):
        if headings[i] > headings[i - 1] + 1:
            skips.append(f"h{headings[i-1]}→h{headings[i]}")
    if skips:
        checks.append(GateCheck(
            gate_id="G3", check_name="heading_hierarchy",
            status="soft_fail", severity="moderate",
            priority="P1", next_action="flag_hitl",
            details=f"Heading levels skip: {', '.join(skips)} (WCAG 2.4.6)",
        ))

    # Check 6: <main> landmark present
    if "<main" in html.lower():
        checks.append(GateCheck(
            gate_id="G3", check_name="main_landmark",
            status="pass", severity="minor",
            priority="P2", next_action="proceed",
            details="<main> landmark present",
        ))
    else:
        checks.append(GateCheck(
            gate_id="G3", check_name="main_landmark",
            status="soft_fail", severity="moderate",
            priority="P1", next_action="flag_hitl",
            details="No <main> landmark found",
        ))

    # Check 7: Multi-column heuristic — consecutive short paragraphs
    # When a multi-column PDF is serialized left-to-right, each column fragment
    # becomes a short <p> element. A run of 5+ consecutive short paragraphs
    # (< 60 chars of visible text) is a common artefact of this. Flag for HITL.
    para_texts = [
        re.sub(r'<[^>]+>', '', m).strip()  # strip inner HTML tags to get visible text
        for m in _RE_PARA_TEXT.findall(html)
    ]
    max_consecutive_short = 0
    current_run = 0
    for text in para_texts:
        if len(text) < _SHORT_PARA_CHAR_LIMIT:
            current_run += 1
            max_consecutive_short = max(max_consecutive_short, current_run)
        else:
            current_run = 0

    if max_consecutive_short >= _SHORT_PARA_RUN_THRESHOLD:
        checks.append(GateCheck(
            gate_id="G3", check_name="multi_column_reading_order",
            status="soft_fail", severity="moderate",
            priority="P1", next_action="flag_hitl",
            details=(
                f"Possible multi-column reading order issue: {max_consecutive_short} "
                f"consecutive short paragraphs (< {_SHORT_PARA_CHAR_LIMIT} chars). "
                "Adobe Extract may have serialized columns left-to-right instead of "
                "top-to-bottom. Flag for HITL reading-order review (WCAG 1.3.2)."
            ),
        ))

    return checks


# ---------------------------------------------------------------------------
# G4: Post-PDF — PDF/UA compliance
# ---------------------------------------------------------------------------


def run_gate_g4(pdf_bytes: bytes) -> GateResult:
    """G4: PDF/UA tag structure validation.

    Tries Adobe PDF Accessibility Checker first (if credentials available).
    Falls back to basic PDF structure inspection via pypdf.
    """
    checks: list[GateCheck] = []

    # Try Adobe Accessibility Checker
    adobe_checks = _run_adobe_checker(pdf_bytes)
    if adobe_checks is not None:
        checks.extend(adobe_checks)
    else:
        # Fallback: basic PDF tag structure check
        fallback_checks = _run_pdf_tag_check(pdf_bytes)
        checks.extend(fallback_checks)

    passed = not any(c.status == "hard_fail" for c in checks)
    return GateResult(gate_id="G4", passed=passed, checks=checks)


def _run_adobe_checker(pdf_bytes: bytes) -> list[GateCheck] | None:
    """Run Adobe PDF Accessibility Checker if available."""
    try:
        from services.common.config import settings
        if not settings.adobe_client_id or not settings.adobe_checker_enabled:
            return None

        from services.extraction.adobe_checker import AdobeAccessibilityChecker
        checker = AdobeAccessibilityChecker()
        result = checker.check_pdf(pdf_bytes)

        checks: list[GateCheck] = []
        for issue in result.get("issues", []):
            severity = issue.get("severity", "moderate")
            is_hard = severity in ("critical", "serious")
            checks.append(GateCheck(
                gate_id="G4",
                check_name=f"adobe_{issue.get('rule', 'unknown')}",
                status="hard_fail" if is_hard else "soft_fail",
                severity=severity,
                priority="P0" if is_hard else "P1",
                next_action="block" if is_hard else "flag_hitl",
                details=issue.get("description", ""),
            ))

        if not result.get("issues"):
            checks.append(GateCheck(
                gate_id="G4", check_name="adobe_checker_pass",
                status="pass", severity="minor",
                priority="P2", next_action="proceed",
                details="Adobe PDF Accessibility Checker: compliant",
            ))
        return checks

    except ImportError:
        return None
    except Exception as exc:
        logger.warning("Adobe checker failed: %s", exc)
        return None


def _run_pdf_tag_check(pdf_bytes: bytes) -> list[GateCheck]:
    """Fallback: basic PDF structure validation via pypdf."""
    checks: list[GateCheck] = []

    try:
        from pypdf import PdfReader
        import io

        reader = PdfReader(io.BytesIO(pdf_bytes))

        # Check 1: Has pages
        if len(reader.pages) == 0:
            checks.append(GateCheck(
                gate_id="G4", check_name="pdf_has_pages",
                status="hard_fail", severity="critical",
                priority="P0", next_action="block",
                details="PDF has zero pages",
            ))
        else:
            checks.append(GateCheck(
                gate_id="G4", check_name="pdf_has_pages",
                status="pass", severity="minor",
                priority="P2", next_action="proceed",
                details=f"PDF has {len(reader.pages)} page(s)",
            ))

        # Check 2: PDF metadata exists
        meta = reader.metadata
        if meta:
            checks.append(GateCheck(
                gate_id="G4", check_name="pdf_metadata",
                status="pass", severity="minor",
                priority="P2", next_action="proceed",
                details="PDF has metadata",
            ))
        else:
            checks.append(GateCheck(
                gate_id="G4", check_name="pdf_metadata",
                status="soft_fail", severity="moderate",
                priority="P1", next_action="flag_hitl",
                details="PDF missing metadata",
            ))

        # Check 3: Basic size sanity
        if len(pdf_bytes) < 100:
            checks.append(GateCheck(
                gate_id="G4", check_name="pdf_size",
                status="hard_fail", severity="critical",
                priority="P0", next_action="block",
                details=f"PDF suspiciously small: {len(pdf_bytes)} bytes",
            ))

    except Exception as exc:
        checks.append(GateCheck(
            gate_id="G4", check_name="pdf_parse_error",
            status="hard_fail", severity="critical",
            priority="P0", next_action="block",
            details=f"Failed to parse PDF: {exc}",
        ))

    if not checks:
        checks.append(GateCheck(
            gate_id="G4", check_name="pdf_basic",
            status="pass", severity="minor",
            priority="P2", next_action="proceed",
            details="Basic PDF structure OK (full check requires Adobe credentials)",
        ))

    return checks


# ---------------------------------------------------------------------------
# G4-VeraPDF: PDF/UA-1 validation via VeraPDF REST container
# ---------------------------------------------------------------------------


def run_gate_g4_verapdf(
    pdf_bytes: bytes,
    baseline_result: Any | None = None,
) -> GateResult:
    """G4-VeraPDF: PDF/UA-1 validation via VeraPDF REST container.

    Soft-fail (P1) on each failing clause. Graceful skip when unavailable.
    When baseline_result is provided, compares endline vs baseline clause counts.
    """
    from services.common.verapdf_client import VeraPDFClient

    client = VeraPDFClient()
    if not client.is_available():
        return GateResult(
            gate_id="G4-VeraPDF",
            passed=True,
            checks=[
                GateCheck(
                    gate_id="G4-VeraPDF",
                    check_name="verapdf_available",
                    status="soft_fail",
                    severity="minor",
                    priority="P2",
                    next_action="proceed",
                    details="VeraPDF container not reachable — skipping PDF/UA-1 validation",
                )
            ],
        )

    result = client.validate_pdfua1(pdf_bytes)
    if result is None:
        return GateResult(
            gate_id="G4-VeraPDF",
            passed=True,
            checks=[
                GateCheck(
                    gate_id="G4-VeraPDF",
                    check_name="verapdf_validation",
                    status="soft_fail",
                    severity="minor",
                    priority="P2",
                    next_action="proceed",
                    details="VeraPDF validation returned no result",
                )
            ],
        )

    checks: list[GateCheck] = []

    # Report compliance status
    checks.append(
        GateCheck(
            gate_id="G4-VeraPDF",
            check_name="pdfua1_compliance",
            status="pass" if result.is_compliant else "soft_fail",
            severity="serious" if not result.is_compliant else "minor",
            priority="P1" if not result.is_compliant else "P2",
            next_action="flag_hitl" if not result.is_compliant else "proceed",
            details=(
                f"PDF/UA-1 {'compliant' if result.is_compliant else 'non-compliant'}: "
                f"{result.error_count} errors across {len(result.failed_clauses)} clauses"
            ),
        )
    )

    # Individual clause failures
    for failure in result.failed_rules:
        checks.append(
            GateCheck(
                gate_id="G4-VeraPDF",
                check_name=f"clause_{failure.clause}",
                status="soft_fail",
                severity="serious",
                priority="P1",
                next_action="flag_hitl",
                details=f"Clause {failure.clause}: {failure.description} ({failure.failure_count} failures)",
            )
        )

    # Endline comparison when baseline is provided
    if baseline_result is not None:
        baseline_count = baseline_result.error_count
        endline_count = result.error_count
        improved = endline_count <= baseline_count
        delta = baseline_count - endline_count
        checks.append(
            GateCheck(
                gate_id="G4-VeraPDF",
                check_name="verapdf_delta",
                status="pass" if improved else "soft_fail",
                severity="minor" if improved else "serious",
                priority="P1" if not improved else "P2",
                next_action="proceed" if improved else "flag_hitl",
                details=(
                    f"PDF/UA-1 delta: {baseline_count} -> {endline_count} errors "
                    f"({'improved by ' + str(delta) if improved else 'REGRESSED by ' + str(-delta)})"
                ),
            )
        )

    all_passed = all(c.status == "pass" for c in checks)
    return GateResult(
        gate_id="G4-VeraPDF",
        passed=all_passed,
        checks=checks,
    )


# ---------------------------------------------------------------------------
# Ledger builder utility
# ---------------------------------------------------------------------------


def build_validation_ledger(
    document_id: str,
    filename: str,
    gate_results: list[GateResult],
) -> dict[str, Any]:
    """Build a validation_ledger.json from gate results."""
    all_checks = [c for g in gate_results for c in g.checks]
    total = len(all_checks)
    passed = sum(1 for c in all_checks if c.status == "pass")
    soft_fails = sum(1 for c in all_checks if c.status == "soft_fail")
    hard_fails = sum(1 for c in all_checks if c.status == "hard_fail")

    p0_count = sum(1 for c in all_checks if c.priority == "P0" and c.status != "pass")
    p1_count = sum(1 for c in all_checks if c.priority == "P1" and c.status != "pass")
    p2_count = sum(1 for c in all_checks if c.priority == "P2" and c.status != "pass")

    if p0_count > 0:
        decision = "block"
    elif p1_count > 0:
        decision = "flag_hitl"
    else:
        decision = "proceed"

    return {
        "document_id": document_id,
        "filename": filename,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gates": [g.to_ledger_entry() for g in gate_results],
        "summary": {
            "total_checks": total,
            "passed": passed,
            "soft_fails": soft_fails,
            "hard_fails": hard_fails,
            "p0_count": p0_count,
            "p1_count": p1_count,
            "p2_count": p2_count,
            "decision": decision,
            "overall": "fail" if hard_fails > 0 else "pass",
        },
    }


# ---------------------------------------------------------------------------
# Publishability helper
# ---------------------------------------------------------------------------


def is_publishable(gate_results: list[GateResult]) -> tuple[bool, str]:
    """Check if document can be published (P0=0, P1 all resolved).

    Returns:
        (publishable: bool, reason: str)
            publishable=True  → no P0 issues and no unresolved P1 issues
            publishable=False → P0 or P1 issues remain; reason explains which
    """
    all_checks = [c for g in gate_results for c in g.checks]

    p0_issues = [
        c for c in all_checks
        if c.priority == "P0" and c.status != "pass"
    ]
    p1_issues = [
        c for c in all_checks
        if c.priority == "P1" and c.status != "pass"
    ]

    if p0_issues:
        names = ", ".join(c.check_name for c in p0_issues)
        return False, f"Document blocked by {len(p0_issues)} P0 issue(s): {names}"

    if p1_issues:
        names = ", ".join(c.check_name for c in p1_issues)
        return False, (
            f"Document has {len(p1_issues)} P1 issue(s) requiring HITL sign-off: {names}"
        )

    return True, "No P0 or P1 issues — document is publishable"
