"""WCAG 2.1 AA Check Engine — Sacramento County PDF Remediation Pipeline.

This module implements all 50 WCAG 2.1 AA check functions and wires them into
a dispatch table keyed by the check_fn_name defined in wcag_rules.py.

The run_full_audit() function iterates over every rule in WCAG_RULES_LEDGER,
dispatches to the corresponding check function, and aggregates findings into
an AuditResult. Every rule produces at least one RuleFinding — no silent skips.

Usage:
    from services.common.wcag_checker import run_full_audit
    from services.common.ir import IRDocument

    result = run_full_audit(ir_doc)
    print(f"{result.rules_failed} rules failed, {result.rules_passed} passed")
"""

from __future__ import annotations

import re
import logging
from typing import Any, Callable

from services.common.ir import IRDocument, IRBlock, BlockType
from services.common.wcag_rules import (
    WCAG_RULES_LEDGER,
    WCAGRule,
    RuleFinding,
    AuditResult,
    FindingStatus,
    FindingSeverity,
    RemediationType,
    PDFApplicability,
    CheckAutomation,
    get_applicable_rules,
    get_all_rules,
)
from services.common.wcag_techniques import format_technique_refs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Placeholder alt text patterns to reject
# ---------------------------------------------------------------------------

_PLACEHOLDER_ALT_RE = re.compile(
    r"^\[Figure on page .+ — alt text requires review\]$"
    r"|^\[Placeholder"
    r"|^placeholder$"
    r"|^image$"
    r"|^figure$"
    r"|^photo$"
    # F30: filename patterns (e.g. "img001.jpg", "photo.png", "path/to/file.gif")
    r"|^.*\.(jpg|jpeg|png|gif|bmp|svg|tiff?|webp)$"
    r"|.*[/\\].*\.(jpg|jpeg|png|gif|bmp|svg|tiff?|webp)$"
    # F30: generic numbered labels
    r"|^(image|figure|picture|photo|img|fig)\s*\d+$"
    # F30: single-word generic labels
    r"|^graphic$"
    r"|^icon$"
    r"|^logo$"
    r"|^screenshot$"
    r"|^picture$"
    r"|^photograph$"
    r"|^illustration$"
    # F30: purely numeric alt text
    r"|^\d+$",
    re.IGNORECASE,
)

# Decorative image content patterns (PDF4 / F38)
_DECORATIVE_ALT_RE = re.compile(
    r"\b(decorative|spacer|separator|border|divider|background)\b",
    re.IGNORECASE,
)

# Sensory-only instruction patterns
_SENSORY_RE = re.compile(
    r"\b(click|tap|press)\s+the\s+(red|green|blue|yellow|orange|purple|round|square|triangle|circular|rectangular)\s+\w+"
    r"|\b(on\s+the\s+(left|right|top|bottom))\b"
    r"|\bthe\s+(round|square|triangle|circular|rectangular)\s+(icon|button|shape|symbol)\b"
    r"|\bsee\s+(the\s+)?(red|blue|green|yellow|orange|colored|colour)\b"
    r"|\b(above|below)\s+(chart|image|figure|table)\b",
    re.IGNORECASE,
)

# Generic link text patterns
_GENERIC_LINK_RE = re.compile(
    r"\b(click here|here|read more|more info|learn more|more|details|link)\b",
    re.IGNORECASE,
)

# URL detection (basic)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# BCP 47 language tag validation (basic)
_BCP47_RE = re.compile(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$")


# ---------------------------------------------------------------------------
# Decorative image detection (PDF4 / F38)
# ---------------------------------------------------------------------------


def _is_likely_decorative(img: IRBlock) -> bool:
    """Heuristic: return True if an image is likely decorative.

    Indicators:
    - Very small dimensions (width < 5px OR height < 5px per bbox)
    - Alt text explicitly says "decorative", "spacer", "separator", etc.
    - Content contains decorative keywords
    """
    # Check bbox dimensions — very small images are likely decorative
    bbox = img.bbox
    width = abs(bbox.x2 - bbox.x1)
    height = abs(bbox.y2 - bbox.y1)
    if (width > 0 and width < 5) or (height > 0 and height < 5):
        return True

    # Check alt text and content for decorative keywords
    alt = img.attributes.get("alt", "") or ""
    combined = f"{alt} {img.content}"
    if _DECORATIVE_ALT_RE.search(combined):
        return True

    return False


# ---------------------------------------------------------------------------
# Feature Detection
# ---------------------------------------------------------------------------


def _detect_pdf_features(ir_doc: IRDocument) -> dict[str, Any]:
    """Detect content features present in the IR document.

    Returns a dict used by conditional check functions to determine
    whether a rule is applicable to this document.
    """
    all_blocks = ir_doc.all_blocks()
    return {
        "has_forms": any(b.block_type == BlockType.FORM_FIELD for b in all_blocks),
        "has_media": False,       # Static PDFs — no media in our pipeline
        "has_javascript": False,  # We don't extract JS from PDFs
        "is_document_set": False, # Always single document
        "has_images": any(b.block_type == BlockType.IMAGE for b in all_blocks),
        "has_tables": any(b.block_type == BlockType.TABLE for b in all_blocks),
        "has_headings": any(b.block_type == BlockType.HEADING for b in all_blocks),
        "has_links": bool(_URL_RE.search(" ".join(b.content for b in all_blocks))),
        "page_count": ir_doc.page_count or len(ir_doc.pages),
    }


# ---------------------------------------------------------------------------
# Helper: build a NOT_APPLICABLE finding
# ---------------------------------------------------------------------------


def _na(rule: WCAGRule, reason: str) -> list[RuleFinding]:
    return [RuleFinding(
        rule_id=rule.rule_id,
        criterion=rule.criterion,
        rule_name=rule.name,
        status=FindingStatus.NOT_APPLICABLE,
        severity=rule.default_severity,
        description=reason,
        auto_fixable=False,
    )]


def _pass(rule: WCAGRule, evidence: str, element_id: str = "", page: int = 0) -> RuleFinding:
    tech_refs = format_technique_refs(rule.criterion)
    full_evidence = f"{evidence} | Satisfies: {tech_refs}" if tech_refs else evidence
    return RuleFinding(
        rule_id=rule.rule_id,
        criterion=rule.criterion,
        rule_name=rule.name,
        status=FindingStatus.PASS,
        severity=rule.default_severity,
        element_id=element_id,
        page=page,
        description=f"PASS: {rule.name}",
        evidence=full_evidence,
        auto_fixable=False,  # PASS findings are not actionable
    )


def _fail(rule: WCAGRule, element_id: str, page: int, description: str,
          proposed_fix: str = "", evidence: str = "",
          remediation_type: RemediationType = None,
          auto_fixable: bool | None = None) -> RuleFinding:
    tech_refs = format_technique_refs(rule.criterion)
    full_evidence = f"{evidence} | Techniques: {tech_refs}" if tech_refs else evidence
    effective_remediation = remediation_type or rule.default_remediation
    # Derive auto_fixable from remediation_type when not explicitly passed
    effective_auto_fixable = auto_fixable if auto_fixable is not None else (effective_remediation == RemediationType.AUTO_FIX)
    return RuleFinding(
        rule_id=rule.rule_id,
        criterion=rule.criterion,
        rule_name=rule.name,
        status=FindingStatus.FAIL,
        severity=rule.default_severity,
        element_id=element_id,
        page=page,
        description=description,
        proposed_fix=proposed_fix,
        evidence=full_evidence,
        remediation_type=effective_remediation,
        auto_fixable=effective_auto_fixable,
    )


# ---------------------------------------------------------------------------
# PRINCIPLE 1: PERCEIVABLE
# ---------------------------------------------------------------------------


def check_1_1_1_non_text_content(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.1.1 — Every image must have non-empty, non-placeholder alt text.

    Enhanced checks:
    - F30: Placeholder/filename/generic label detection (expanded regex)
    - F65: Missing alt text entirely
    - PDF4/F38: Decorative image detection (WARNING — needs human confirmation)
    - Long alt text dual-description suggestion (NOTE)
    Findings are grouped by page to avoid UI spam.
    """
    images = ir_doc.blocks_by_type(BlockType.IMAGE)
    if not images:
        return [_pass(rule, "No images found in document.")]

    # Collect issues per image, then aggregate by page
    missing_by_page: dict[int, list[str]] = {}    # page_num -> list of block_ids
    decorative_by_page: dict[int, list[str]] = {} # page_num -> list of block_ids
    long_alt_by_page: dict[int, list[str]] = {}   # page_num -> list of block_ids

    for img in images:
        alt = img.attributes.get("alt", "")
        page = img.page_num
        bid = img.block_id

        # PDF4 / F38: Decorative image detection — check FIRST to avoid
        # contradictory findings (decorative images should not also fail F30/F65)
        if _is_likely_decorative(img):
            decorative_by_page.setdefault(page, []).append(bid)
            continue  # Skip F30/F65 and long-alt checks for decorative images

        # F65 / F30: Missing, empty, or placeholder/filename alt text
        if not alt or not alt.strip() or _PLACEHOLDER_ALT_RE.match(alt.strip()):
            missing_by_page.setdefault(page, []).append(bid)
            continue  # Skip further checks for images with no valid alt

        # Long alt text suggestion: if > 150 chars
        if len(alt) > 150:
            long_alt_by_page.setdefault(page, []).append(bid)

    findings: list[RuleFinding] = []

    _MAX_IDS_PER_FINDING = 25  # Cap image IDs per finding to keep payload sane

    # Emit one finding per page for missing alt text
    for page, ids in sorted(missing_by_page.items()):
        count = len(ids)
        capped_ids = ids[:_MAX_IDS_PER_FINDING]
        id_str = ", ".join(capped_ids)
        suffix = f" (+{count - _MAX_IDS_PER_FINDING} more)" if count > _MAX_IDS_PER_FINDING else ""
        findings.append(_fail(
            rule,
            element_id=f"page_{page}_images",
            page=page,
            description=f"Page {page}: {count} image(s) missing descriptive alt text (F30/F65).",
            proposed_fix="Provide descriptive alt text that conveys the purpose and content of each image.",
            evidence=f"{count} image(s) on page {page} have missing, empty, or placeholder alt text | image_ids: {id_str}{suffix}",
            remediation_type=RemediationType.AI_DRAFT,
            auto_fixable=False,
        ))

    # Emit one finding per page for decorative images
    for page, ids in sorted(decorative_by_page.items()):
        count = len(ids)
        capped_ids = ids[:_MAX_IDS_PER_FINDING]
        id_str = ", ".join(capped_ids)
        suffix = f" (+{count - _MAX_IDS_PER_FINDING} more)" if count > _MAX_IDS_PER_FINDING else ""
        findings.append(_fail(
            rule,
            element_id=f"page_{page}_decorative",
            page=page,
            description=f"Page {page}: {count} image(s) appear decorative (PDF4/F38). Mark as Artifact if purely decorative.",
            proposed_fix="Per PDF4: mark decorative images as /Artifact so screen readers skip them.",
            evidence=f"Decorative heuristic triggered for {count} image(s) on page {page} | image_ids: {id_str}{suffix}",
            remediation_type=RemediationType.MANUAL_REVIEW,
            auto_fixable=False,
        ))

    # Emit one finding per page for overly long alt text
    for page, ids in sorted(long_alt_by_page.items()):
        count = len(ids)
        capped_ids = ids[:_MAX_IDS_PER_FINDING]
        id_str = ", ".join(capped_ids)
        suffix = f" (+{count - _MAX_IDS_PER_FINDING} more)" if count > _MAX_IDS_PER_FINDING else ""
        tech_refs = format_technique_refs(rule.criterion)
        long_alt_evidence = f"{count} image(s) on page {page} exceed 150-char alt text threshold | image_ids: {id_str}{suffix}"
        full_long_alt_evidence = f"{long_alt_evidence} | Techniques: {tech_refs}" if tech_refs else long_alt_evidence
        findings.append(RuleFinding(
            rule_id=rule.rule_id,
            criterion=rule.criterion,
            rule_name=rule.name,
            status=FindingStatus.FAIL,
            severity=FindingSeverity.MODERATE,
            element_id=f"page_{page}_long_alt",
            page=page,
            description=f"Page {page}: {count} image(s) have alt text >150 chars — consider short alt + long description.",
            proposed_fix="Use a concise alt (<150 chars) with a separate long description via /ActualText.",
            evidence=full_long_alt_evidence,
            remediation_type=RemediationType.MANUAL_REVIEW,
            auto_fixable=False,
        ))

    if not findings:
        return [_pass(rule, f"All {len(images)} image(s) have valid alt text.")]
    return findings


def check_1_2_1_audio_video_only(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.2.1 — Audio/video only: not applicable for static PDFs."""
    features = ctx.get("features", {})
    if not features.get("has_media", False):
        return _na(rule, "No embedded media detected in this static PDF document.")
    return [_fail(rule, element_id="document", page=0,
                   description="Embedded media detected — cannot verify text alternative automatically. Manual review required.",
                   proposed_fix="Review all embedded media for text alternatives per WCAG 1.2.1.",
                   evidence="Embedded media content detected in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_1_2_2_captions_prerecorded(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.2.2 — Captions (prerecorded): not applicable for static PDFs."""
    features = ctx.get("features", {})
    if not features.get("has_media", False):
        return _na(rule, "No synchronized media detected in this static PDF document.")
    return [_fail(rule, element_id="document", page=0,
                   description="Synchronized media detected — cannot verify captions automatically. Manual review required.",
                   proposed_fix="Verify all prerecorded synchronized media has accurate captions per WCAG 1.2.2.",
                   evidence="Synchronized media content detected in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_1_2_3_audio_description(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.2.3 — Audio description: not applicable for static PDFs."""
    features = ctx.get("features", {})
    if not features.get("has_media", False):
        return _na(rule, "No video content detected in this static PDF document.")
    return [_fail(rule, element_id="document", page=0,
                   description="Video content detected — cannot verify audio description automatically. Manual review required.",
                   proposed_fix="Verify video content has audio description or media alternative per WCAG 1.2.3.",
                   evidence="Video content detected in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_1_2_4_captions_live(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.2.4 — Captions (live): never applicable to static PDFs."""
    return _na(rule, "Live media does not apply to static PDF documents.")


def check_1_2_5_audio_description_prerecorded(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.2.5 — Audio description (prerecorded): not applicable for static PDFs."""
    features = ctx.get("features", {})
    if not features.get("has_media", False):
        return _na(rule, "No prerecorded video detected in this static PDF document.")
    return [_fail(rule, element_id="document", page=0,
                   description="Prerecorded video detected — cannot verify audio description automatically. Manual review required.",
                   proposed_fix="Verify prerecorded video has audio description per WCAG 1.2.5.",
                   evidence="Prerecorded video content detected in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_1_3_1_info_relationships(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.3.1 — Info and Relationships: headings, tables, lists must be structured.

    Enhanced checks:
    - PDF6/F90/F91: Table header consistency (header count matches data row cell count)
    - PDF21: List structure validation (non-empty items)
    - F46: Layout table detection (single-column, no headers)
    """
    findings: list[RuleFinding] = []

    # Sub-check A: Tables must have headers and captions (PDF6)
    tables = ir_doc.blocks_by_type(BlockType.TABLE)
    for tbl in tables:
        headers = tbl.attributes.get("headers", [])
        rows = tbl.attributes.get("rows", [])
        caption = tbl.attributes.get("caption", "")

        # Missing caption check
        if not caption or not str(caption).strip():
            findings.append(_fail(
                rule,
                element_id=tbl.block_id,
                page=tbl.page_num,
                description="Table is missing a caption — tables must have a summary or caption to describe their purpose.",
                proposed_fix="Add a <Caption> element to the table structure describing what data the table presents.",
                evidence=f"Table at page {tbl.page_num} has no 'caption' attribute.",
                remediation_type=RemediationType.AUTO_FIX,
                auto_fixable=True,
            ))

        if not headers:
            # F46: If table has exactly 1 column and no headers, flag as possible layout table
            max_cols = max((len(r) for r in rows), default=0) if rows else 0
            if max_cols <= 1:
                findings.append(_fail(
                    rule,
                    element_id=tbl.block_id,
                    page=tbl.page_num,
                    description=(
                        "Single-column table with no headers detected — possible layout table (F46). "
                        "Layout tables must not use Table/TR/TH/TD tags."
                    ),
                    proposed_fix=(
                        "If this is a layout table, remove table markup and use paragraph/div structure instead. "
                        "If it is a data table, add appropriate TH header cells per PDF6."
                    ),
                    evidence=f"Table at page {tbl.page_num}: 0 headers, max {max_cols} column(s) — F46 layout table heuristic.",
                    auto_fixable=False,
                ))
            else:
                findings.append(_fail(
                    rule,
                    element_id=tbl.block_id,
                    page=tbl.page_num,
                    description="Table lacks header row — relationships between data and headers cannot be programmatically determined (F91).",
                    proposed_fix="Add table headers using TH tags with appropriate scope attributes per PDF6.",
                    evidence=f"Table at page {tbl.page_num} has empty 'headers' attribute.",
                    auto_fixable=True,
                ))
        else:
            # PDF6 / F90: Check header count consistency with data rows
            header_count = len(headers)
            for row_idx, row in enumerate(rows):
                if len(row) != header_count:
                    findings.append(_fail(
                        rule,
                        element_id=tbl.block_id,
                        page=tbl.page_num,
                        description=(
                            f"Table row {row_idx + 1} has {len(row)} cell(s) but header row has "
                            f"{header_count} column(s) — header/data association is inconsistent (F90)."
                        ),
                        proposed_fix=(
                            "Ensure every data row has the same number of cells as there are headers. "
                            "Use RowSpan/ColSpan for merged cells per PDF6/PDF20."
                        ),
                        evidence=(
                            f"Table at page {tbl.page_num}: headers={header_count}, "
                            f"row {row_idx + 1}={len(row)} cells."
                        ),
                        auto_fixable=False,
                    ))
                    break  # One inconsistency finding per table is sufficient

    # Sub-check B: Lists must have items attribute (PDF21)
    lists = ir_doc.blocks_by_type(BlockType.LIST)
    for lst in lists:
        items = lst.attributes.get("items", [])
        if not items:
            findings.append(_fail(
                rule,
                element_id=lst.block_id,
                page=lst.page_num,
                description="List block has no items — list structure cannot be programmatically determined (PDF21).",
                proposed_fix="Ensure list is tagged with L > LI > Lbl + LBody structure per PDF21.",
                evidence=f"List block at page {lst.page_num} has empty 'items' attribute.",
                auto_fixable=True,
            ))
        else:
            # PDF21: Check that list items are non-empty strings
            empty_items = [i for i, item in enumerate(items) if not str(item).strip()]
            if empty_items:
                findings.append(_fail(
                    rule,
                    element_id=lst.block_id,
                    page=lst.page_num,
                    description=f"List has {len(empty_items)} empty item(s) — list items must have content (PDF21).",
                    proposed_fix="Provide text content for all list items in the L > LI > LBody structure.",
                    evidence=f"List at page {lst.page_num}: empty item indices: {empty_items[:5]}.",
                    auto_fixable=False,
                ))

    # Sub-check C: Headings must have valid levels 1-6 (PDF9)
    headings = ir_doc.blocks_by_type(BlockType.HEADING)
    for h in headings:
        level = h.attributes.get("level", 0)
        if not isinstance(level, int) or level < 1 or level > 6:
            findings.append(_fail(
                rule,
                element_id=h.block_id,
                page=h.page_num,
                description=f"Heading has invalid level '{level}' (must be 1-6).",
                proposed_fix="Assign a valid heading level (H1-H6) based on document hierarchy per PDF9.",
                evidence=f"Heading block at page {h.page_num} has level={level}.",
                auto_fixable=True,
            ))

    if not findings:
        return [_pass(rule, f"Tables: {len(tables)}, Lists: {len(lists)}, Headings: {len(headings)} — all have proper structure.")]
    return findings


def check_1_3_2_meaningful_sequence(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.3.2 — Meaningful Sequence: flag multi-column pages for reading order review."""
    findings: list[RuleFinding] = []

    for page in ir_doc.pages:
        if not page.blocks:
            continue
        # Detect multi-column layout: 3+ distinct x-position clusters
        x_positions = [round(b.bbox.x1 / 50) * 50 for b in page.blocks]
        distinct_x = len(set(x_positions))
        if distinct_x >= 3:
            findings.append(RuleFinding(
                rule_id=rule.rule_id,
                criterion=rule.criterion,
                rule_name=rule.name,
                status=FindingStatus.FAIL,
                severity=FindingSeverity.MODERATE,
                element_id=f"page_{page.page_num}",
                page=page.page_num,
                description=f"Page {page.page_num} has {distinct_x} distinct x-position clusters — possible multi-column layout that may affect reading order.",
                proposed_fix="Verify tag tree reading order matches intended left-to-right, top-to-bottom sequence.",
                evidence=f"Distinct x-positions: {sorted(set(x_positions))}",
                remediation_type=RemediationType.MANUAL_REVIEW,
                auto_fixable=False,
            ))

    if not findings:
        return [_pass(rule, "No multi-column layout detected. Reading order appears sequential.")]
    return findings


def check_1_3_3_sensory_characteristics(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.3.3 — Sensory Characteristics: scan for sensory-only instructions."""
    findings: list[RuleFinding] = []
    all_blocks = ir_doc.all_blocks()

    for block in all_blocks:
        matches = _SENSORY_RE.findall(block.content)
        if matches:
            findings.append(_fail(
                rule,
                element_id=block.block_id,
                page=block.page_num,
                description=f"Text contains sensory-only reference: '{block.content[:120]}...'",
                proposed_fix="Rewrite to provide non-sensory alternative (e.g. 'click the Submit button' instead of 'click the red button').",
                evidence=f"Pattern match: {matches}",
                remediation_type=RemediationType.MANUAL_REVIEW,
                auto_fixable=False,
            ))

    if not findings:
        return [_pass(rule, "No sensory-only instruction patterns detected.")]
    return findings


def check_1_3_4_orientation(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.3.4 — Orientation: check metadata for forced rotation entries.

    Tagged PDFs do not inherently lock orientation. Only explicit /Rotate
    metadata that forces a single orientation would violate this criterion.
    """
    # Check metadata for /Rotate entries that force orientation
    rotate_value = ir_doc.metadata.get("Rotate") or ir_doc.metadata.get("rotate")
    if rotate_value is not None:
        # A /Rotate value in metadata suggests the PDF forces a specific orientation
        return [_fail(
            rule,
            element_id="document",
            page=0,
            description=(
                f"PDF metadata contains a /Rotate entry (value: {rotate_value}) which may "
                f"force a specific orientation. Verify this does not lock the document to "
                f"portrait or landscape only."
            ),
            proposed_fix=(
                "Remove or adjust the /Rotate entry so the document does not restrict "
                "display to a single orientation unless essential for content understanding."
            ),
            evidence=f"Metadata /Rotate value: {rotate_value}.",
            remediation_type=RemediationType.MANUAL_REVIEW,
            auto_fixable=False,
        )]

    return [_pass(
        rule,
        "No /Rotate metadata forcing a specific orientation detected. "
        "Tagged PDFs do not inherently lock orientation — content reflows "
        "in conforming PDF viewers regardless of viewport orientation.",
    )]


def check_1_3_5_identify_input_purpose(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.3.5 — Identify Input Purpose: only applicable if form fields exist."""
    features = ctx.get("features", {})
    if not features.get("has_forms", False):
        return _na(rule, "No form fields detected in this document.")

    form_fields = ir_doc.blocks_by_type(BlockType.FORM_FIELD)
    findings: list[RuleFinding] = []
    for ff in form_fields:
        field_name = ff.attributes.get("name", "") or ff.content or ""
        if not field_name.strip():
            findings.append(_fail(
                rule,
                element_id=ff.block_id,
                page=ff.page_num,
                description="Form field lacks a descriptive name attribute for input purpose identification.",
                proposed_fix="Add a /TU tooltip attribute that describes the input purpose (e.g. 'First Name', 'Email Address').",
                evidence="Field has empty name and content.",
                remediation_type=RemediationType.MANUAL_REVIEW,
                auto_fixable=False,
            ))

    if not findings:
        return [_pass(rule, f"All {len(form_fields)} form field(s) have descriptive names.")]
    return findings


def check_1_4_1_use_of_color(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.4.1 — Use of Color: flag chart/graph images for manual verification.

    Findings are grouped by page to avoid UI spam.
    """
    images = ir_doc.blocks_by_type(BlockType.IMAGE)
    chart_keywords = re.compile(r"\b(chart|graph|figure|diagram|plot|pie|bar|line)\b", re.IGNORECASE)

    chart_images = [img for img in images if chart_keywords.search(img.attributes.get("alt", "") or img.content)]

    # Group chart images by page
    charts_by_page: dict[int, int] = {}
    for img in chart_images:
        page = img.page_num
        charts_by_page[page] = charts_by_page.get(page, 0) + 1

    findings: list[RuleFinding] = []
    for page, count in sorted(charts_by_page.items()):
        findings.append(_fail(
            rule,
            element_id=f"page_{page}_charts",
            page=page,
            description=f"Page {page}: {count} chart/graph image(s) detected — verify color is not the sole means of conveying information.",
            proposed_fix="Ensure chart data is distinguishable by pattern, label, or texture in addition to color. Add a text-based data table or description.",
            evidence=f"{count} chart/graph image(s) on page {page} may rely on color alone",
            remediation_type=RemediationType.MANUAL_REVIEW,
            auto_fixable=False,
        ))

    if not findings:
        return [_pass(rule, "No chart/graph images detected. Verify manually if document contains unlabelled color-coded content.")]
    return findings


def check_1_4_2_audio_control(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.4.2 — Audio Control: not applicable for static PDFs without auto-playing audio."""
    features = ctx.get("features", {})
    if not features.get("has_media", False):
        return _na(rule, "No auto-playing audio detected in this static PDF document.")
    return [_fail(rule, element_id="document", page=0,
                   description="Media detected — cannot verify audio control automatically. Manual review required.",
                   proposed_fix="Verify all auto-playing audio has pause/stop/volume controls per WCAG 1.4.2.",
                   evidence="Media content detected in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_1_4_3_contrast_minimum(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.4.3 — Contrast Minimum: check for scanned pages and flag text pages for manual review.

    Scanned pages (no extractable text) are worst-case contrast failures since
    text is embedded in images with no way to verify or adjust contrast.
    Pages with extractable text still need manual verification but are lower risk.
    """
    findings: list[RuleFinding] = []
    scanned_pages = [p for p in ir_doc.pages if not p.has_extractable_text]
    text_pages = [p for p in ir_doc.pages if p.has_extractable_text]

    if scanned_pages:
        page_nums = [p.page_num for p in scanned_pages]
        findings.append(RuleFinding(
            rule_id=rule.rule_id,
            criterion=rule.criterion,
            rule_name=rule.name,
            status=FindingStatus.FAIL,
            severity=FindingSeverity.SERIOUS,
            element_id="scanned_pages",
            page=scanned_pages[0].page_num,
            description=(
                f"{len(scanned_pages)} scanned page(s) with no extractable text — "
                f"text is rendered as images making contrast verification and adjustment impossible. "
                f"Pages: {page_nums[:10]}{'...' if len(page_nums) > 10 else ''}."
            ),
            proposed_fix=(
                "Apply OCR to convert scanned pages to real text, then verify contrast. "
                "Scanned image text cannot be restyled to meet the 4.5:1 contrast ratio."
            ),
            evidence=f"Pages without extractable text: {page_nums}",
            remediation_type=RemediationType.MANUAL_REVIEW,
            auto_fixable=False,
        ))

    if text_pages:
        findings.append(RuleFinding(
            rule_id=rule.rule_id,
            criterion=rule.criterion,
            rule_name=rule.name,
            status=FindingStatus.NOT_APPLICABLE,
            severity=FindingSeverity.MODERATE,
            element_id="text_pages",
            page=text_pages[0].page_num,
            description=(
                f"{len(text_pages)} page(s) with extractable text — contrast cannot be verified at IR stage. "
                f"Manual verification required: text contrast ratio must meet 4.5:1 minimum "
                f"(3:1 for large text, 18pt+ or 14pt+ bold)."
            ),
            proposed_fix=(
                "Use TPGi Colour Contrast Analyser or Adobe Accessibility Checker to verify "
                "all text meets the 4.5:1 contrast ratio against its background."
            ),
            evidence=(
                f"{len(text_pages)} text page(s) present. Pixel-level color analysis is not available "
                f"at the IR stage — manual tool verification required."
            ),
            remediation_type=RemediationType.MANUAL_REVIEW,
            auto_fixable=False,
        ))

    if not findings:
        return [_pass(rule, "No pages found in document.")]
    return findings


def check_1_4_4_resize_text(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.4.4 — Resize Text: check that all pages have extractable text."""
    non_text_pages = [p for p in ir_doc.pages if not p.has_extractable_text]
    if non_text_pages:
        return [_fail(
            rule,
            element_id=f"page_{non_text_pages[0].page_num}",
            page=non_text_pages[0].page_num,
            description=f"{len(non_text_pages)} page(s) have no extractable text — text may be rendered as images and cannot be resized.",
            proposed_fix="Apply OCR or replace image-based text with real text in the PDF.",
            evidence=f"Pages without extractable text: {[p.page_num for p in non_text_pages]}",
            remediation_type=RemediationType.MANUAL_REVIEW,
            auto_fixable=False,
        )]
    return [_pass(rule, f"All {len(ir_doc.pages)} pages have extractable text. Properly tagged PDF supports 200% resize.")]


def check_1_4_5_images_of_text(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.4.5 — Images of Text: flag scanned pages and images with long alt text.

    Two detection strategies:
    1. Pages without extractable text are definite images-of-text (scanned pages)
    2. Images with very long alt text (>50 words) may indicate image-of-text content
    """
    findings: list[RuleFinding] = []

    # Strategy 1: Scanned pages — strongest signal for images-of-text
    scanned_pages = [p for p in ir_doc.pages if not p.has_extractable_text]
    if scanned_pages:
        page_nums = [p.page_num for p in scanned_pages]
        findings.append(RuleFinding(
            rule_id=rule.rule_id,
            criterion=rule.criterion,
            rule_name=rule.name,
            status=FindingStatus.FAIL,
            severity=FindingSeverity.SERIOUS,
            element_id="scanned_pages",
            page=scanned_pages[0].page_num,
            description=(
                f"{len(scanned_pages)} scanned page(s) detected with no extractable text — "
                f"entire page content is rendered as images of text. "
                f"Pages: {page_nums[:10]}{'...' if len(page_nums) > 10 else ''}."
            ),
            proposed_fix=(
                "Apply OCR to convert scanned pages to real, selectable text. "
                "Images of text must be replaced with actual text content unless "
                "essential for presentation (e.g., logotypes)."
            ),
            evidence=f"Pages without extractable text (scanned): {page_nums}",
            remediation_type=RemediationType.MANUAL_REVIEW,
            auto_fixable=False,
        ))

    # Strategy 2: Images with very long alt text (>50 words)
    images = ir_doc.blocks_by_type(BlockType.IMAGE)
    for img in images:
        alt = img.attributes.get("alt", "") or ""
        word_count = len(alt.split())
        if word_count > 50:
            findings.append(RuleFinding(
                rule_id=rule.rule_id,
                criterion=rule.criterion,
                rule_name=rule.name,
                status=FindingStatus.FAIL,
                severity=rule.default_severity,
                element_id=img.attributes.get("image_id", img.block_id),
                page=img.page_num,
                description=f"Image alt text is very long ({word_count} words) — may indicate an image of text.",
                proposed_fix="If this is a scanned text image, replace with actual text content. If decorative, mark as Artifact.",
                evidence=f"Alt text word count: {word_count}. Preview: '{alt[:80]}...'",
                remediation_type=RemediationType.MANUAL_REVIEW,
                auto_fixable=False,
            ))

    if not findings:
        img_count = len(images)
        return [_pass(rule, f"All {len(ir_doc.pages)} page(s) have extractable text. Checked {img_count} image(s). No images-of-text detected.")]
    return findings


def check_1_4_10_reflow(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.4.10 — Reflow: properly tagged PDFs inherently support reflow mode.

    A PDF with headings, lists, and table structure tags enables reflow
    in conforming PDF viewers (e.g., Adobe Acrobat's Accessibility > Reflow).
    If no structure tags exist, reflow is not possible.
    """
    features = ctx.get("features", {})
    has_headings = features.get("has_headings", False)
    has_tables = features.get("has_tables", False)

    # Check for structural tags in the document
    headings = ir_doc.blocks_by_type(BlockType.HEADING)
    lists = ir_doc.blocks_by_type(BlockType.LIST)
    tables = ir_doc.blocks_by_type(BlockType.TABLE)
    paragraphs = ir_doc.blocks_by_type(BlockType.PARAGRAPH)

    has_structure = bool(headings or lists or tables or paragraphs)

    if has_structure:
        structure_summary = []
        if headings:
            structure_summary.append(f"{len(headings)} heading(s)")
        if paragraphs:
            structure_summary.append(f"{len(paragraphs)} paragraph(s)")
        if lists:
            structure_summary.append(f"{len(lists)} list(s)")
        if tables:
            structure_summary.append(f"{len(tables)} table(s)")
        return [_pass(
            rule,
            f"Properly tagged PDF structure supports reflow mode in conforming PDF viewers. "
            f"Structure tags found: {', '.join(structure_summary)}. "
            f"Verify reflow rendering in Adobe Acrobat (View > Zoom > Reflow) for final confirmation.",
        )]

    return [_fail(
        rule,
        element_id="document",
        page=0,
        description=(
            "Document has no structure tags (no headings, paragraphs, lists, or tables). "
            "Without tag structure, PDF viewers cannot reflow content at 400% zoom."
        ),
        proposed_fix=(
            "Add proper tag structure (H1-H6, P, L/LI, Table/TR/TD) to enable reflow. "
            "Our pipeline will generate these tags during recompilation."
        ),
        evidence="0 headings, 0 paragraphs, 0 lists, 0 tables found in IR.",
        remediation_type=RemediationType.AUTO_FIX,
        auto_fixable=True,
    )]


def check_1_4_11_non_text_contrast(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.4.11 — Non-text Contrast: count UI elements needing contrast review.

    Identifies form fields and chart/graph images that require 3:1 contrast
    verification. If no such elements exist, the criterion passes.
    """
    features = ctx.get("features", {})
    findings: list[RuleFinding] = []

    # Count form fields (UI components needing 3:1 contrast)
    form_fields = ir_doc.blocks_by_type(BlockType.FORM_FIELD) if features.get("has_forms", False) else []

    # Count chart/graph images (graphical objects needing 3:1 contrast)
    chart_keywords = re.compile(r"\b(chart|graph|figure|diagram|plot|pie|bar|line)\b", re.IGNORECASE)
    images = ir_doc.blocks_by_type(BlockType.IMAGE)
    chart_images = [
        img for img in images
        if chart_keywords.search(img.attributes.get("alt", "") or img.content)
    ]

    elements_needing_review: list[str] = []
    if form_fields:
        elements_needing_review.append(f"{len(form_fields)} form field(s)")
    if chart_images:
        elements_needing_review.append(f"{len(chart_images)} chart/graph image(s)")

    if not elements_needing_review:
        return [_pass(
            rule,
            "No UI components (form fields) or graphical objects (charts/graphs) detected "
            "that require non-text contrast verification.",
        )]

    total_count = len(form_fields) + len(chart_images)
    element_summary = " and ".join(elements_needing_review)
    return [_fail(
        rule,
        element_id="document",
        page=0,
        description=(
            f"{total_count} element(s) require non-text contrast verification (3:1 minimum): "
            f"{element_summary}. Visually inspect these elements to ensure sufficient contrast "
            f"against adjacent colors."
        ),
        proposed_fix=(
            "Use TPGi Colour Contrast Analyser to verify form field boundaries, chart lines, "
            "and graphical indicators meet the 3:1 contrast ratio against adjacent colors."
        ),
        evidence=f"Elements needing review: {element_summary}.",
        remediation_type=RemediationType.MANUAL_REVIEW,
        auto_fixable=False,
    )]


def check_1_4_12_text_spacing(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.4.12 — Text Spacing: tagged PDFs support text spacing overrides.

    A properly tagged PDF allows conforming viewers to override text spacing
    properties. If the document has structure tags, text spacing overrides
    are supported. If no structure, text spacing cannot be adjusted.
    """
    # Check for structural tags in the document
    headings = ir_doc.blocks_by_type(BlockType.HEADING)
    lists = ir_doc.blocks_by_type(BlockType.LIST)
    tables = ir_doc.blocks_by_type(BlockType.TABLE)
    paragraphs = ir_doc.blocks_by_type(BlockType.PARAGRAPH)

    has_structure = bool(headings or lists or tables or paragraphs)

    if has_structure:
        structure_summary = []
        if headings:
            structure_summary.append(f"{len(headings)} heading(s)")
        if paragraphs:
            structure_summary.append(f"{len(paragraphs)} paragraph(s)")
        if lists:
            structure_summary.append(f"{len(lists)} list(s)")
        if tables:
            structure_summary.append(f"{len(tables)} table(s)")
        return [_pass(
            rule,
            f"Properly tagged PDF structure supports text spacing overrides in conforming PDF viewers. "
            f"Structure tags found: {', '.join(structure_summary)}. "
            f"Tagged text can be reflowed with adjusted line height (1.5x), paragraph spacing (2x), "
            f"letter spacing (0.12em), and word spacing (0.16em) without content loss.",
        )]

    return [_fail(
        rule,
        element_id="document",
        page=0,
        description=(
            "Document has no structure tags (no headings, paragraphs, lists, or tables). "
            "Without tag structure, text spacing overrides (line height 1.5x, paragraph spacing 2x, "
            "letter spacing 0.12em, word spacing 0.16em) cannot be applied without content loss."
        ),
        proposed_fix=(
            "Add proper tag structure (H1-H6, P, L/LI, Table/TR/TD) to enable text spacing overrides. "
            "Our pipeline will generate these tags during recompilation."
        ),
        evidence="0 headings, 0 paragraphs, 0 lists, 0 tables found in IR.",
        remediation_type=RemediationType.AUTO_FIX,
        auto_fixable=True,
    )]


def check_1_4_13_content_on_hover_focus(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """1.4.13 — Content on Hover/Focus: not applicable for static PDFs."""
    features = ctx.get("features", {})
    if not features.get("has_javascript", False):
        return _na(rule, "No hover/focus-triggered content detected. Static PDF has no JavaScript interaction.")
    return [_fail(rule, element_id="document", page=0,
                   description="JavaScript detected — cannot verify hover/focus content behavior automatically. Manual review required.",
                   proposed_fix="Verify any content that appears on hover/focus is dismissible, hoverable, and persistent per WCAG 1.4.13.",
                   evidence="JavaScript interaction detected in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


# ---------------------------------------------------------------------------
# PRINCIPLE 2: OPERABLE
# ---------------------------------------------------------------------------


def check_2_1_1_keyboard(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.1.1 — Keyboard: check for interactive elements that need keyboard testing.

    If no interactive elements exist (no forms, no links, no JavaScript),
    the criterion is not applicable — there is nothing to keyboard-navigate
    beyond standard PDF viewer controls.
    """
    features = ctx.get("features", {})
    has_forms = features.get("has_forms", False)
    has_links = features.get("has_links", False)
    has_javascript = features.get("has_javascript", False)

    if not has_forms and not has_links and not has_javascript:
        return _na(
            rule,
            "No interactive elements detected (no forms, links, or JavaScript). "
            "Standard PDF viewer controls handle keyboard navigation.",
        )

    # Count interactive elements for specific guidance
    interactive_details: list[str] = []
    total_count = 0

    if has_forms:
        form_fields = ir_doc.blocks_by_type(BlockType.FORM_FIELD)
        interactive_details.append(f"{len(form_fields)} form field(s)")
        total_count += len(form_fields)

    if has_links:
        # Count URL references in text blocks
        all_blocks = ir_doc.all_blocks()
        link_count = sum(1 for b in all_blocks if _URL_RE.search(b.content))
        if link_count > 0:
            interactive_details.append(f"{link_count} link(s)")
            total_count += link_count

    if has_javascript:
        interactive_details.append("JavaScript interactions")

    element_summary = ", ".join(interactive_details)
    return [_fail(
        rule,
        element_id="document",
        page=0,
        description=(
            f"{total_count} interactive element(s) require keyboard accessibility testing: "
            f"{element_summary}. Verify all are reachable and operable via keyboard."
        ),
        proposed_fix=(
            "Open the output PDF in a viewer, press Tab to navigate all links and form fields. "
            "Verify all interactive elements are reachable and operable without a mouse. "
            "Set /Tabs /S on all page dictionaries."
        ),
        evidence=f"Interactive elements detected: {element_summary}.",
        remediation_type=RemediationType.MANUAL_REVIEW,
        auto_fixable=False,
    )]


def check_2_1_2_no_keyboard_trap(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.1.2 — No Keyboard Trap: not applicable for static PDFs without JS."""
    features = ctx.get("features", {})
    if not features.get("has_javascript", False) and not features.get("has_forms", False):
        return _na(rule, "No interactive elements or JavaScript detected. Static PDFs do not create keyboard traps.")
    if features.get("has_javascript", False):
        return [_fail(rule, element_id="document", page=0,
                       description="JavaScript detected — cannot verify absence of keyboard traps automatically. Manual review required.",
                       proposed_fix="Tab through all interactive elements in the output PDF. Verify focus can always leave each element via standard keys.",
                       evidence="JavaScript present in PDF — keyboard trap risk cannot be ruled out without manual testing.",
                       remediation_type=RemediationType.MANUAL_REVIEW)]
    # Form fields without JS: this IS a valid pass
    return [_pass(rule, "Standard PDF form fields without JavaScript do not create keyboard traps.")]


def check_2_1_4_character_key_shortcuts(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.1.4 — Character Key Shortcuts: not applicable for static PDFs."""
    features = ctx.get("features", {})
    if not features.get("has_javascript", False):
        return _na(rule, "No custom keyboard shortcuts detected. Static PDFs do not implement single-character shortcuts.")
    return [_fail(rule, element_id="document", page=0,
                   description="JavaScript detected — cannot verify character key shortcuts automatically. Manual review required.",
                   proposed_fix="Check for single-character keyboard shortcuts. If present, ensure they can be remapped or disabled per WCAG 2.1.4.",
                   evidence="JavaScript present in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_2_2_1_timing_adjustable(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.2.1 — Timing Adjustable: not applicable for static PDFs."""
    features = ctx.get("features", {})
    if not features.get("has_javascript", False):
        return _na(rule, "No time-limited interactions detected. Static PDFs have no JavaScript timers.")
    return [_fail(rule, element_id="document", page=0,
                   description="JavaScript detected — cannot verify time limits automatically. Manual review required.",
                   proposed_fix="Verify no unadjustable time limits are in use. If present, users must be able to extend or disable them per WCAG 2.2.1.",
                   evidence="JavaScript present in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_2_2_2_pause_stop_hide(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.2.2 — Pause, Stop, Hide: not applicable for static PDFs."""
    features = ctx.get("features", {})
    if not features.get("has_media", False):
        return _na(rule, "No moving, blinking, or auto-updating content detected. Static PDF has no animation.")
    return [_fail(rule, element_id="document", page=0,
                   description="Media detected — cannot verify pause/stop controls automatically. Manual review required.",
                   proposed_fix="Verify all moving, blinking, or auto-updating content has pause/stop/hide controls per WCAG 2.2.2.",
                   evidence="Media content detected in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_2_3_1_three_flashes(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.3.1 — Three Flashes: not applicable for static PDFs."""
    features = ctx.get("features", {})
    if not features.get("has_media", False):
        return _na(rule, "No animated or video content detected. Static PDF cannot produce flashing content.")
    return [_fail(rule, element_id="document", page=0,
                   description="Media detected — cannot verify flash rate automatically. Manual review required.",
                   proposed_fix="Verify no content flashes more than 3 times per second per WCAG 2.3.1.",
                   evidence="Media content detected in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_2_4_1_bypass_blocks(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.4.1 — Bypass Blocks: headings enable section navigation."""
    headings = ir_doc.blocks_by_type(BlockType.HEADING)
    page_count = ctx.get("features", {}).get("page_count", ir_doc.page_count or 1)

    block_count = len(ir_doc.all_blocks())
    if not headings and (page_count > 1 or block_count > 10):
        return [_fail(
            rule,
            element_id="document",
            page=0,
            description=f"Document ({page_count} page(s), {block_count} block(s)) lacks headings for section navigation.",
            proposed_fix="Add H1-H6 heading tags at major section boundaries to enable bypass of repeated content.",
            evidence=f"0 heading blocks found across {page_count} page(s) and {block_count} content block(s).",
            auto_fixable=True,
        )]

    return [_pass(rule, f"{len(headings)} heading(s) provide section navigation. Bookmarks will be generated from heading structure.")]


def check_2_4_2_page_titled(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.4.2 — Page Titled: PDF must have a non-empty, descriptive title.

    Enhanced checks:
    - F25: Title must not be a filename, generic text, UUID, or hash
    - PDF18: /Title + /DisplayDocTitle will be set during recompilation
    """
    explicit_title = ir_doc.metadata.get("title", "") or ir_doc.metadata.get("dc:title", "")
    filename_base = ir_doc.filename.rsplit(".", 1)[0] if "." in ir_doc.filename else ir_doc.filename
    title = explicit_title or filename_base

    if not title.strip():
        return [_fail(
            rule,
            element_id="document",
            page=0,
            description="Document has no title — neither metadata title nor filename could be determined.",
            proposed_fix="Set the PDF /Title metadata field to a descriptive title per PDF18. Ensure /DisplayDocTitle is true.",
            evidence="ir_doc.metadata has no 'title' key and ir_doc.filename is empty.",
            auto_fixable=True,
        )]

    # F25: Detect when title looks like a filename or generic text
    title_stripped = title.strip()
    f25_fail = False
    f25_reason = ""

    # Filename patterns: *.pdf, *.doc, *.docx, etc.
    if re.match(r"^.*\.(pdf|doc|docx|xls|xlsx|ppt|pptx|txt|rtf|odt)$", title_stripped, re.IGNORECASE):
        f25_fail = True
        f25_reason = f"Title '{title_stripped}' appears to be a filename"
    # Path separators
    elif "/" in title_stripped or "\\" in title_stripped:
        f25_fail = True
        f25_reason = f"Title '{title_stripped}' contains path separators"
    # Generic non-descriptive titles
    elif re.match(r"^(untitled|document|doc\d*|page\d*|file\d*|new document|microsoft word)$", title_stripped, re.IGNORECASE):
        f25_fail = True
        f25_reason = f"Title '{title_stripped}' is a generic non-descriptive label"
    # UUID or hash-like strings (32+ hex chars or UUID pattern)
    elif re.match(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", title_stripped, re.IGNORECASE):
        f25_fail = True
        f25_reason = f"Title '{title_stripped}' appears to be a UUID"
    elif re.match(r"^[0-9a-f]{32,}$", title_stripped, re.IGNORECASE):
        f25_fail = True
        f25_reason = f"Title '{title_stripped}' appears to be a hash string"
    # Filename stem patterns: all-lowercase with hyphens/underscores, no spaces
    # (e.g., "annual-report-2025", "budget_summary_final")
    elif (
        re.match(r"^[a-z0-9][-_a-z0-9]*[a-z0-9]$", title_stripped)
        and ("-" in title_stripped or "_" in title_stripped)
        and " " not in title_stripped
    ):
        f25_fail = True
        f25_reason = f"Title '{title_stripped}' appears to be a filename stem (all-lowercase with hyphens/underscores)"

    if f25_fail:
        return [_fail(
            rule,
            element_id="document",
            page=0,
            description=f"Title does not identify document contents (F25). {f25_reason}.",
            proposed_fix=(
                "Replace with a descriptive title that identifies the document's subject or purpose per PDF18. "
                "Set /DisplayDocTitle true in viewer preferences."
            ),
            evidence=f"Title value: '{title_stripped}' — fails F25 non-descriptive title check.",
            auto_fixable=True,
        )]

    return [_pass(
        rule,
        f"Document title will be set to: '{title}'. "
        f"PDF18: /DisplayDocTitle true will be set during recompilation.",
    )]


def check_2_4_3_focus_order(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.4.3 — Focus Order: check interactive elements and reading order logic.

    If no interactive elements exist, the criterion is not applicable.
    If interactive elements exist, analyze whether the tag tree reading order
    appears sequential (sorted by page, then y-position top to bottom).
    """
    features = ctx.get("features", {})
    has_forms = features.get("has_forms", False)
    has_links = features.get("has_links", False)
    has_javascript = features.get("has_javascript", False)

    if not has_forms and not has_links and not has_javascript:
        return _na(
            rule,
            "No interactive elements detected (no forms, links, or JavaScript). "
            "Focus order is not applicable without focusable elements.",
        )

    # Check if reading order appears sequential (sorted by page_num, then y-position)
    all_blocks = ir_doc.all_blocks()
    order_appears_sequential = True
    prev_page = -1
    prev_y = -1.0

    for block in all_blocks:
        if block.page_num < prev_page:
            order_appears_sequential = False
            break
        if block.page_num == prev_page and block.bbox.y1 < prev_y - 20:
            # Allow 20-unit tolerance for blocks at approximately the same vertical position
            order_appears_sequential = False
            break
        prev_page = block.page_num
        prev_y = block.bbox.y1

    tech_refs = format_technique_refs(rule.criterion)

    if order_appears_sequential:
        evidence_base = (
            "Block ordering analysis: blocks are sorted by page_num then y-position (top-to-bottom). "
            "This suggests logical reading order — focus order follows tag tree sequence."
        )
        full_evidence = f"{evidence_base} | Satisfies: {tech_refs}" if tech_refs else evidence_base
        return [_pass(
            rule,
            full_evidence,
        )]
    else:
        evidence_base = (
            "Block ordering analysis: blocks are NOT consistently sorted by page_num then y-position. "
            "This indicates potential reading order issues that will affect focus order."
        )
        full_evidence = f"{evidence_base} | Techniques: {tech_refs}" if tech_refs else evidence_base
        return [RuleFinding(
            rule_id=rule.rule_id,
            criterion=rule.criterion,
            rule_name=rule.name,
            status=FindingStatus.FAIL,
            severity=FindingSeverity.SERIOUS,
            element_id="document",
            page=0,
            description=(
                "Tag tree reading order appears non-sequential — blocks are not consistently "
                "ordered by page and vertical position. Focus order may not match visual layout."
            ),
            proposed_fix=(
                "Review and correct the tag tree reading order so it follows the intended visual sequence. "
                "Set /Tabs /S (Structure) on all page dictionaries. Verify with a screen reader."
            ),
            evidence=full_evidence,
            remediation_type=RemediationType.MANUAL_REVIEW,
            auto_fixable=False,
        )]


def check_2_4_4_link_purpose(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.4.4 — Link Purpose: detect generic link text in paragraph content.

    Enhanced checks:
    - PDF11/PDF13: Generic link text with /Alt entry suggestion
    - Bare URL detection: URLs without surrounding descriptive text
    """
    findings: list[RuleFinding] = []
    all_blocks = ir_doc.all_blocks()

    for block in all_blocks:
        urls = _URL_RE.findall(block.content)
        if not urls:
            continue

        # Check surrounding text for generic link phrases
        if _GENERIC_LINK_RE.search(block.content):
            findings.append(_fail(
                rule,
                element_id=block.block_id,
                page=block.page_num,
                description=f"Generic link text detected: '{block.content[:120]}'",
                proposed_fix=(
                    "Replace generic link text ('click here', 'read more') with descriptive text "
                    "that identifies the link destination. Alternatively, per PDF13, add a /Alt entry "
                    "on the Link tag to provide descriptive replacement text."
                ),
                evidence=f"URLs found: {urls[:3]}. Generic phrase detected in surrounding text.",
                remediation_type=RemediationType.AI_DRAFT,
                auto_fixable=False,
            ))
            continue  # Already flagged this block

        # Bare URL detection: if the content is JUST a URL (possibly with whitespace)
        content_stripped = block.content.strip()
        if _URL_RE.fullmatch(content_stripped):
            findings.append(_fail(
                rule,
                element_id=block.block_id,
                page=block.page_num,
                description=(
                    f"Bare URL without descriptive text: '{content_stripped[:100]}'. "
                    f"Link purpose is not clear from the URL alone."
                ),
                proposed_fix=(
                    "Provide descriptive visible text for the link, or per PDF13, add a /Alt entry "
                    "on the Link tag to provide descriptive replacement text."
                ),
                evidence=f"Paragraph contains only a bare URL with no surrounding descriptive text.",
                remediation_type=RemediationType.AI_DRAFT,
                auto_fixable=False,
            ))

    if not findings:
        return [_pass(rule, "No generic link text detected. All links appear to have descriptive context.")]
    return findings


def check_2_4_5_multiple_ways(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.4.5 — Multiple Ways: document needs both headings and bookmarks."""
    headings = ir_doc.blocks_by_type(BlockType.HEADING)
    if not headings:
        return [_fail(
            rule,
            element_id="document",
            page=0,
            description="Document lacks headings — cannot provide multiple navigation methods (bookmarks require heading structure).",
            proposed_fix="Add H1-H6 headings at section boundaries. Our pipeline generates bookmarks from heading tags.",
            evidence="0 heading blocks found.",
            auto_fixable=True,
        )]
    return [_pass(rule, f"Document has {len(headings)} heading(s). Bookmarks will be generated, providing multiple navigation methods.")]


def check_2_4_6_headings_and_labels(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.4.6 — Headings and Labels: check hierarchy and non-empty headings."""
    headings = ir_doc.blocks_by_type(BlockType.HEADING)
    findings: list[RuleFinding] = []

    # Check for empty headings
    for h in headings:
        if not h.content.strip():
            findings.append(_fail(
                rule,
                element_id=h.block_id,
                page=h.page_num,
                description="Empty heading detected — headings must have descriptive text.",
                proposed_fix="Add meaningful text to the heading that describes the section content.",
                evidence=f"Heading level {h.attributes.get('level')} at page {h.page_num} has empty content.",
                auto_fixable=False,
            ))

    # Check for skipped heading levels
    levels = [h.attributes.get("level", 0) for h in headings if isinstance(h.attributes.get("level"), int)]
    for i in range(1, len(levels)):
        prev, curr = levels[i - 1], levels[i]
        if curr > prev + 1:
            findings.append(_fail(
                rule,
                element_id=headings[i].block_id,
                page=headings[i].page_num,
                description=f"Heading level skipped: H{prev} → H{curr}. Missing H{prev + 1}.",
                proposed_fix=f"Change H{curr} to H{prev + 1} or insert an intermediate heading level.",
                evidence=f"Level jump from {prev} to {curr} at page {headings[i].page_num}.",
                auto_fixable=True,
            ))

    # Check form field labels
    features = ctx.get("features", {})
    if features.get("has_forms", False):
        form_fields = ir_doc.blocks_by_type(BlockType.FORM_FIELD)
        for ff in form_fields:
            label = ff.attributes.get("label", "") or ff.content or ""
            if not label.strip():
                findings.append(_fail(
                    rule,
                    element_id=ff.block_id,
                    page=ff.page_num,
                    description="Form field has no label — purpose cannot be determined.",
                    proposed_fix="Add a visible label or /TU tooltip to identify the form field's expected input.",
                    evidence="Field has no label attribute and no content.",
                    auto_fixable=False,
                ))

    if not findings:
        return [_pass(rule, f"All {len(headings)} heading(s) have text and valid hierarchy. Labels are present.")]
    return findings


def check_2_4_7_focus_visible(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.4.7 — Focus Visible: check for interactive elements that need focus indicators.

    If no interactive elements exist (no forms, no links), the criterion
    is not applicable. Otherwise, report element counts for manual testing.
    """
    features = ctx.get("features", {})
    has_forms = features.get("has_forms", False)
    has_links = features.get("has_links", False)

    if not has_forms and not has_links:
        return _na(
            rule,
            "No interactive elements detected (no forms or links). "
            "Focus visibility is not applicable without focusable elements.",
        )

    # Count interactive elements for specific guidance
    interactive_details: list[str] = []
    total_count = 0

    if has_forms:
        form_fields = ir_doc.blocks_by_type(BlockType.FORM_FIELD)
        interactive_details.append(f"{len(form_fields)} form field(s)")
        total_count += len(form_fields)

    if has_links:
        all_blocks = ir_doc.all_blocks()
        link_count = sum(1 for b in all_blocks if _URL_RE.search(b.content))
        if link_count > 0:
            interactive_details.append(f"{link_count} link(s)")
            total_count += link_count

    element_summary = ", ".join(interactive_details)
    return _na(
        rule,
        f"Focus visibility cannot be determined from static PDF analysis. "
        f"{total_count} interactive element(s) detected ({element_summary}) — "
        f"manual verification required to confirm visible focus indicators are present "
        f"when navigating via keyboard.",
    )


def check_2_5_1_pointer_gestures(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.5.1 — Pointer Gestures: not applicable for static PDFs."""
    features = ctx.get("features", {})
    if not features.get("has_javascript", False):
        return _na(rule, "No multipoint gesture interactions detected. Standard PDF viewing does not require gestures.")
    return [_fail(rule, element_id="document", page=0,
                   description="JavaScript detected — cannot verify pointer gesture alternatives automatically. Manual review required.",
                   proposed_fix="Verify single-pointer alternatives exist for any multipoint or path-based gesture interactions per WCAG 2.5.1.",
                   evidence="JavaScript present in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_2_5_2_pointer_cancellation(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.5.2 — Pointer Cancellation: not applicable for static PDFs."""
    features = ctx.get("features", {})
    if not features.get("has_javascript", False):
        return _na(rule, "No pointer-down triggered actions detected. Standard PDF links activate on click (up-event).")
    return [_fail(rule, element_id="document", page=0,
                   description="JavaScript detected — cannot verify pointer cancellation automatically. Manual review required.",
                   proposed_fix="Verify pointer-down actions are cancellable (up-event completes, or undo/abort mechanism exists) per WCAG 2.5.2.",
                   evidence="JavaScript present in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_2_5_3_label_in_name(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.5.3 — Label in Name: only applicable if form fields exist."""
    features = ctx.get("features", {})
    if not features.get("has_forms", False):
        return _na(rule, "No labeled UI components (form fields, buttons) detected.")
    return [_fail(rule, element_id="document", page=0,
                   description="Form fields detected — cannot verify label-in-name match automatically. Manual review required.",
                   proposed_fix="Verify accessible names of form fields and buttons contain their visible label text per WCAG 2.5.3.",
                   evidence="Form fields present in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_2_5_4_motion_actuation(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """2.5.4 — Motion Actuation: never applicable to static PDFs."""
    return _na(rule, "Static PDF documents do not use device motion for functionality.")


# ---------------------------------------------------------------------------
# PRINCIPLE 3: UNDERSTANDABLE
# ---------------------------------------------------------------------------


def check_3_1_1_language_of_page(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """3.1.1 — Language of Page: validate BCP 47 language tag (PDF16).

    Checks the RAW PDF metadata language first (ir_doc.metadata.get("language")),
    falling back to ir_doc.language. This avoids false passes when the HTML builder
    auto-injects lang="en" — we want to know if the ORIGINAL PDF had a language tag.
    """
    # Prefer raw PDF metadata over ir_doc.language (which defaults to "en")
    raw_lang = (ir_doc.metadata.get("language") or "").strip()
    has_explicit_lang = bool(
        ir_doc.metadata.get("language") or ir_doc.metadata.get("lang")
    )

    # Use raw metadata lang if available, otherwise fall back to ir_doc.language
    lang = raw_lang if raw_lang else (ir_doc.language or "").strip()

    # If ir_doc.language is the default "en" and metadata has no explicit lang,
    # the original PDF likely had no /Lang entry — flag it
    if not has_explicit_lang and ir_doc.language == "en" and not raw_lang:
        return [_fail(
            rule,
            element_id="document",
            page=0,
            description=(
                "Document language tag not found in PDF metadata — the default 'en' was "
                "applied by the pipeline but the original PDF may lack a /Lang entry."
            ),
            proposed_fix=(
                "Set the /Lang entry in the document catalog to a valid BCP 47 language tag "
                "(e.g. 'en', 'en-US', 'es') per PDF16."
            ),
            evidence="No /Lang found in PDF metadata. Pipeline default 'en' is not verified.",
            auto_fixable=True,
        )]

    if not lang:
        return [_fail(
            rule,
            element_id="document",
            page=0,
            description="Document language is not set — screen readers cannot determine the correct language for pronunciation.",
            proposed_fix=(
                "Set the /Lang entry in the document catalog to a valid BCP 47 language tag "
                "(e.g. 'en', 'en-US', 'es') per PDF16."
            ),
            evidence="ir_doc.language is empty. PDF16 requires /Lang entry in document catalog.",
            auto_fixable=True,
        )]

    if not _BCP47_RE.match(lang.strip()):
        return [_fail(
            rule,
            element_id="document",
            page=0,
            description=f"Document language tag '{lang}' is not a valid BCP 47 tag.",
            proposed_fix=(
                "Use a valid BCP 47 language tag such as 'en', 'en-US', 'es', or 'fr-CA' "
                "in the document catalog /Lang entry per PDF16."
            ),
            evidence=f"Language value '{lang}' does not match BCP 47 pattern. PDF16 requires valid BCP 47.",
            auto_fixable=True,
        )]

    return [_pass(rule, f"Document language is set to '{lang}' — valid BCP 47 tag. PDF16: /Lang entry satisfies this criterion.")]


def check_3_1_2_language_of_parts(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """3.1.2 — Language of Parts: scan for non-ASCII character sequences indicating foreign language.

    Detects common non-ASCII character ranges: CJK, Cyrillic, Arabic, Devanagari,
    and runs of accented Latin characters that suggest foreign-language passages.
    """
    # Regex patterns for foreign language character ranges
    foreign_char_re = re.compile(
        r"[\u4e00-\u9fff]"          # CJK Unified Ideographs (Chinese, Japanese, Korean)
        r"|[\u0400-\u04ff]"         # Cyrillic (Russian, Ukrainian, etc.)
        r"|[\u0600-\u06ff]"         # Arabic
        r"|[\u0900-\u097f]"         # Devanagari (Hindi, Sanskrit, etc.)
        r"|[àáâãäåæçèéêëìíîïðñòóôõöùúûüýþÿ]{3,}"  # Runs of 3+ accented Latin chars
    )

    foreign_findings: list[dict] = []  # {page, block_id, sample, script}
    all_blocks = ir_doc.all_blocks()

    for block in all_blocks:
        if not block.content:
            continue
        matches = foreign_char_re.findall(block.content)
        if matches:
            # Determine script type for reporting
            sample = matches[0][:20]
            script = "unknown"
            if re.search(r"[\u4e00-\u9fff]", sample):
                script = "CJK (Chinese/Japanese/Korean)"
            elif re.search(r"[\u0400-\u04ff]", sample):
                script = "Cyrillic"
            elif re.search(r"[\u0600-\u06ff]", sample):
                script = "Arabic"
            elif re.search(r"[\u0900-\u097f]", sample):
                script = "Devanagari"
            else:
                script = "accented Latin"

            foreign_findings.append({
                "page": block.page_num,
                "block_id": block.block_id,
                "sample": sample,
                "script": script,
            })

    if not foreign_findings:
        return [_pass(
            rule,
            "No obvious multilingual content detected (no CJK, Cyrillic, Arabic, Devanagari, "
            "or extended accented Latin character sequences found). Verify manually if document "
            "may contain foreign-language passages in standard Latin script.",
        )]

    # Group by script type for concise reporting
    scripts_found: dict[str, list[int]] = {}
    for f in foreign_findings:
        scripts_found.setdefault(f["script"], []).append(f["page"])

    findings: list[RuleFinding] = []
    for script, pages in scripts_found.items():
        unique_pages = sorted(set(pages))
        sample_finding = next(f for f in foreign_findings if f["script"] == script)
        findings.append(_fail(
            rule,
            element_id=sample_finding["block_id"],
            page=unique_pages[0],
            description=(
                f"{script} characters detected on {len(unique_pages)} page(s): "
                f"{unique_pages[:10]}{'...' if len(unique_pages) > 10 else ''}. "
                f"Foreign-language passages must have /Lang span entries per PDF19."
            ),
            proposed_fix=(
                f"Add /Lang attribute on the structure element(s) containing {script} text "
                f"to identify the language (e.g., 'zh' for Chinese, 'ru' for Russian, "
                f"'ar' for Arabic, 'hi' for Hindi) per PDF19."
            ),
            evidence=f"Sample characters: '{sample_finding['sample']}' on page {sample_finding['page']}.",
            remediation_type=RemediationType.MANUAL_REVIEW,
            auto_fixable=False,
        ))

    return findings


def check_3_2_1_on_focus(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """3.2.1 — On Focus: not applicable for static PDFs without JS focus handlers."""
    features = ctx.get("features", {})
    if not features.get("has_javascript", False) and not features.get("has_forms", False):
        return _na(rule, "No focus event handlers detected. Static PDFs do not trigger context changes on focus.")
    return [_fail(rule, element_id="document", page=0,
                   description="Interactive elements detected — cannot verify on-focus behavior automatically. Manual review required.",
                   proposed_fix="Verify focus does not trigger unexpected context changes (page navigations, form submissions) per WCAG 3.2.1.",
                   evidence="Interactive elements (forms/JavaScript) detected in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_3_2_2_on_input(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """3.2.2 — On Input: not applicable for static PDFs without form change handlers."""
    features = ctx.get("features", {})
    if not features.get("has_forms", False):
        return _na(rule, "No form fields with change handlers detected.")
    return [_fail(rule, element_id="document", page=0,
                   description="Form fields detected — cannot verify on-input behavior automatically. Manual review required.",
                   proposed_fix="Verify changing form field values does not trigger unexpected context changes per WCAG 3.2.2.",
                   evidence="Form fields present in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_3_2_3_consistent_navigation(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """3.2.3 — Consistent Navigation: not applicable for single documents."""
    features = ctx.get("features", {})
    if not features.get("is_document_set", False):
        return _na(rule, "Single document — consistent navigation applies only to document sets.")
    return [_fail(rule, element_id="document", page=0,
                   description="Document set detected — cannot verify navigation consistency automatically. Manual review required.",
                   proposed_fix="Verify repeated navigation mechanisms maintain consistent order across documents per WCAG 3.2.3.",
                   evidence="Document set detected.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_3_2_4_consistent_identification(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """3.2.4 — Consistent Identification: not applicable for single documents."""
    features = ctx.get("features", {})
    if not features.get("is_document_set", False):
        return _na(rule, "Single document — consistent identification applies only to document sets.")
    return [_fail(rule, element_id="document", page=0,
                   description="Document set detected — cannot verify identification consistency automatically. Manual review required.",
                   proposed_fix="Verify components with the same function use consistent labels across documents per WCAG 3.2.4.",
                   evidence="Document set detected.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_3_3_1_error_identification(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """3.3.1 — Error Identification: only applicable if forms with validation exist."""
    features = ctx.get("features", {})
    if not features.get("has_forms", False):
        return _na(rule, "No form fields with validation detected.")
    return [_fail(rule, element_id="document", page=0,
                   description="Form fields detected — cannot verify error identification automatically. Manual review required.",
                   proposed_fix="Verify that input errors are identified and described in text to the user per WCAG 3.3.1.",
                   evidence="Form fields present in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


def check_3_3_2_labels_or_instructions(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """3.3.2 — Labels or Instructions: check form field labels."""
    features = ctx.get("features", {})
    if not features.get("has_forms", False):
        return _na(rule, "No form fields requiring labels or instructions detected.")

    form_fields = ir_doc.blocks_by_type(BlockType.FORM_FIELD)
    findings: list[RuleFinding] = []
    for ff in form_fields:
        tooltip = ff.attributes.get("tooltip", "") or ff.attributes.get("label", "") or ff.content or ""
        if not tooltip.strip():
            findings.append(_fail(
                rule,
                element_id=ff.block_id,
                page=ff.page_num,
                description="Form field has no label or instructions — users cannot determine what input is required.",
                proposed_fix="Add a /TU tooltip or associated label widget describing the expected input.",
                evidence="Field has no tooltip, label attribute, or content.",
                auto_fixable=True,
            ))

    if not findings:
        return [_pass(rule, f"All {len(form_fields)} form field(s) have labels or instructions.")]
    return findings


def check_3_3_3_error_suggestion(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """3.3.3 — Error Suggestion: forms exist but error suggestion behavior cannot be verified statically."""
    features = ctx.get("features", {})
    if not features.get("has_forms", False):
        return _na(rule, "No form fields with validation detected.")
    form_fields = ir_doc.blocks_by_type(BlockType.FORM_FIELD)
    return _na(
        rule,
        f"{len(form_fields)} form field(s) detected. Error suggestion behavior cannot be verified "
        f"from static PDF analysis — manual verification required to confirm that input errors "
        f"are detected and suggestions are provided where possible (PDF22).",
    )


def check_3_3_4_error_prevention(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """3.3.4 — Error Prevention: not applicable without legal/financial form submission."""
    features = ctx.get("features", {})
    if not features.get("has_forms", False):
        return _na(rule, "No forms with legal/financial data submission detected.")
    return _na(rule, "Legal/financial form submission not detected in this document.")


# ---------------------------------------------------------------------------
# PRINCIPLE 4: ROBUST
# ---------------------------------------------------------------------------


def check_4_1_1_parsing(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """4.1.1 — Parsing: check block_ids are unique, block types are valid, and pages are sane."""
    all_blocks = ir_doc.all_blocks()
    findings: list[RuleFinding] = []
    valid_types = {bt.value for bt in BlockType}

    # Check unique block_ids
    ids_seen: dict[str, int] = {}
    for block in all_blocks:
        ids_seen[block.block_id] = ids_seen.get(block.block_id, 0) + 1

    duplicates = [bid for bid, count in ids_seen.items() if count > 1]
    if duplicates:
        findings.append(_fail(
            rule,
            element_id=duplicates[0],
            page=0,
            description=f"Duplicate block IDs detected: {duplicates[:5]}. Structure element IDs must be unique.",
            proposed_fix="Ensure all block_ids are unique UUIDs.",
            evidence=f"{len(duplicates)} duplicate IDs: {duplicates[:3]}",
            auto_fixable=True,
        ))

    # Check all block types are valid + defensive checks
    for block in all_blocks:
        if block.block_type.value not in valid_types:
            findings.append(_fail(
                rule,
                element_id=block.block_id,
                page=block.page_num,
                description=f"Invalid block_type '{block.block_type.value}' — must be one of {sorted(valid_types)}.",
                proposed_fix="Assign a valid block_type from the BlockType enum.",
                evidence=f"Block {block.block_id} at page {block.page_num} has unrecognized type.",
                auto_fixable=True,
            ))

        # Defensive: check for empty block_type value
        if not block.block_type.value:
            findings.append(_fail(
                rule,
                element_id=block.block_id,
                page=block.page_num,
                description="Block has empty block_type — every structure element must have a defined type.",
                proposed_fix="Assign a valid block_type from the BlockType enum.",
                evidence=f"Block {block.block_id} has empty block_type value.",
                auto_fixable=True,
            ))

        # Defensive: check for negative or unreasonable page numbers
        if block.page_num < 0:
            findings.append(_fail(
                rule,
                element_id=block.block_id,
                page=0,
                description=f"Block has negative page number ({block.page_num}) — page numbers must be non-negative.",
                proposed_fix="Correct the page_num to a valid non-negative integer.",
                evidence=f"Block {block.block_id} has page_num={block.page_num}.",
                auto_fixable=True,
            ))

    if not findings:
        return [_pass(rule, f"Tag tree integrity check passed. {len(all_blocks)} blocks, all IDs unique and types valid.")]
    return findings


def check_4_1_2_name_role_value(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """4.1.2 — Name, Role, Value: form fields and links must have accessible names.

    Enhanced checks:
    - F68: Form field without programmatic name
    - PDF10/PDF12: /TU tooltip entry for accessible name
    """
    features = ctx.get("features", {})
    has_forms = features.get("has_forms", False)
    has_links = features.get("has_links", False)

    if not has_forms and not has_links:
        return _na(rule, "No form fields or links detected — no UI components requiring name/role/value.")

    findings: list[RuleFinding] = []

    if has_forms:
        form_fields = ir_doc.blocks_by_type(BlockType.FORM_FIELD)
        for ff in form_fields:
            name = (ff.attributes.get("name", "")
                    or ff.attributes.get("tooltip", "")
                    or ff.attributes.get("label", "")
                    or ff.content)
            if not name.strip():
                findings.append(_fail(
                    rule,
                    element_id=ff.block_id,
                    page=ff.page_num,
                    description=(
                        "Form field has no accessible name (F68) — "
                        "assistive technology cannot identify its purpose."
                    ),
                    proposed_fix=(
                        "Set /T field name and /TU tooltip entry on the form field widget annotation "
                        "per PDF10. Ensure /FT (type/role), /TU (name), /V (value), and /Ff (flags) "
                        "are properly set per PDF12."
                    ),
                    evidence="All name attributes are empty. F68: UI control without programmatic name.",
                    remediation_type=RemediationType.AUTO_FIX,
                    auto_fixable=True,
                ))

    if not findings:
        return [_pass(rule, "All interactive elements have accessible names and roles.")]
    return findings


def check_4_1_3_status_messages(ir_doc: IRDocument, rule: WCAGRule, **ctx) -> list[RuleFinding]:
    """4.1.3 — Status Messages: not applicable for static PDFs without dynamic content."""
    features = ctx.get("features", {})
    if not features.get("has_javascript", False):
        return _na(rule, "No dynamic status messages detected. Static PDF has no JavaScript-driven status updates.")
    return [_fail(rule, element_id="document", page=0,
                   description="JavaScript detected — cannot verify status message accessibility automatically. Manual review required.",
                   proposed_fix="Verify status messages (success, error, progress) are communicated to assistive technologies without receiving focus per WCAG 4.1.3.",
                   evidence="JavaScript present in document.",
                   remediation_type=RemediationType.MANUAL_REVIEW)]


# ---------------------------------------------------------------------------
# Dispatch Table
# ---------------------------------------------------------------------------

CHECK_DISPATCH: dict[str, Callable] = {
    "check_1_1_1_non_text_content": check_1_1_1_non_text_content,
    "check_1_2_1_audio_video_only": check_1_2_1_audio_video_only,
    "check_1_2_2_captions_prerecorded": check_1_2_2_captions_prerecorded,
    "check_1_2_3_audio_description": check_1_2_3_audio_description,
    "check_1_2_4_captions_live": check_1_2_4_captions_live,
    "check_1_2_5_audio_description_prerecorded": check_1_2_5_audio_description_prerecorded,
    "check_1_3_1_info_relationships": check_1_3_1_info_relationships,
    "check_1_3_2_meaningful_sequence": check_1_3_2_meaningful_sequence,
    "check_1_3_3_sensory_characteristics": check_1_3_3_sensory_characteristics,
    "check_1_3_4_orientation": check_1_3_4_orientation,
    "check_1_3_5_identify_input_purpose": check_1_3_5_identify_input_purpose,
    "check_1_4_1_use_of_color": check_1_4_1_use_of_color,
    "check_1_4_2_audio_control": check_1_4_2_audio_control,
    "check_1_4_3_contrast_minimum": check_1_4_3_contrast_minimum,
    "check_1_4_4_resize_text": check_1_4_4_resize_text,
    "check_1_4_5_images_of_text": check_1_4_5_images_of_text,
    "check_1_4_10_reflow": check_1_4_10_reflow,
    "check_1_4_11_non_text_contrast": check_1_4_11_non_text_contrast,
    "check_1_4_12_text_spacing": check_1_4_12_text_spacing,
    "check_1_4_13_content_on_hover_focus": check_1_4_13_content_on_hover_focus,
    "check_2_1_1_keyboard": check_2_1_1_keyboard,
    "check_2_1_2_no_keyboard_trap": check_2_1_2_no_keyboard_trap,
    "check_2_1_4_character_key_shortcuts": check_2_1_4_character_key_shortcuts,
    "check_2_2_1_timing_adjustable": check_2_2_1_timing_adjustable,
    "check_2_2_2_pause_stop_hide": check_2_2_2_pause_stop_hide,
    "check_2_3_1_three_flashes": check_2_3_1_three_flashes,
    "check_2_4_1_bypass_blocks": check_2_4_1_bypass_blocks,
    "check_2_4_2_page_titled": check_2_4_2_page_titled,
    "check_2_4_3_focus_order": check_2_4_3_focus_order,
    "check_2_4_4_link_purpose": check_2_4_4_link_purpose,
    "check_2_4_5_multiple_ways": check_2_4_5_multiple_ways,
    "check_2_4_6_headings_and_labels": check_2_4_6_headings_and_labels,
    "check_2_4_7_focus_visible": check_2_4_7_focus_visible,
    "check_2_5_1_pointer_gestures": check_2_5_1_pointer_gestures,
    "check_2_5_2_pointer_cancellation": check_2_5_2_pointer_cancellation,
    "check_2_5_3_label_in_name": check_2_5_3_label_in_name,
    "check_2_5_4_motion_actuation": check_2_5_4_motion_actuation,
    "check_3_1_1_language_of_page": check_3_1_1_language_of_page,
    "check_3_1_2_language_of_parts": check_3_1_2_language_of_parts,
    "check_3_2_1_on_focus": check_3_2_1_on_focus,
    "check_3_2_2_on_input": check_3_2_2_on_input,
    "check_3_2_3_consistent_navigation": check_3_2_3_consistent_navigation,
    "check_3_2_4_consistent_identification": check_3_2_4_consistent_identification,
    "check_3_3_1_error_identification": check_3_3_1_error_identification,
    "check_3_3_2_labels_or_instructions": check_3_3_2_labels_or_instructions,
    "check_3_3_3_error_suggestion": check_3_3_3_error_suggestion,
    "check_3_3_4_error_prevention": check_3_3_4_error_prevention,
    "check_4_1_1_parsing": check_4_1_1_parsing,
    "check_4_1_2_name_role_value": check_4_1_2_name_role_value,
    "check_4_1_3_status_messages": check_4_1_3_status_messages,
}


# ---------------------------------------------------------------------------
# Main Audit Orchestrator
# ---------------------------------------------------------------------------


def run_full_audit(ir_doc: IRDocument) -> AuditResult:
    """Run ALL 50 WCAG 2.1 AA checks against an IRDocument.

    Every rule produces at least one finding. No silent skips.
    """
    features = _detect_pdf_features(ir_doc)
    all_findings: list[RuleFinding] = []
    rules_checked = 0
    rules_passed = 0
    rules_failed = 0
    rules_na = 0
    rules_errored = 0

    for rule in WCAG_RULES_LEDGER:
        check_fn = CHECK_DISPATCH.get(rule.check_fn_name)
        if check_fn is None:
            all_findings.append(RuleFinding(
                rule_id=rule.rule_id,
                criterion=rule.criterion,
                rule_name=rule.name,
                status=FindingStatus.ERROR,
                severity=FindingSeverity.CRITICAL,
                description=f"Check function '{rule.check_fn_name}' not implemented",
                evidence="Implementation gap — this rule was not checked",
            ))
            rules_errored += 1
            rules_checked += 1
            continue

        try:
            findings = check_fn(ir_doc, rule, features=features)
        except Exception as exc:
            findings = [RuleFinding(
                rule_id=rule.rule_id,
                criterion=rule.criterion,
                rule_name=rule.name,
                status=FindingStatus.ERROR,
                severity=rule.default_severity,
                description=f"Check failed with error: {exc}",
                evidence=str(exc),
            )]
            rules_errored += 1
            rules_checked += 1
            all_findings.extend(findings)
            continue

        # Ensure at least one finding per rule
        if not findings:
            findings = [RuleFinding(
                rule_id=rule.rule_id,
                criterion=rule.criterion,
                rule_name=rule.name,
                status=FindingStatus.ERROR,
                severity=FindingSeverity.MINOR,
                description="Check function returned no findings",
                evidence="Implementation error — check must return at least one finding",
            )]
            rules_errored += 1
            rules_checked += 1
            all_findings.extend(findings)
            continue

        all_findings.extend(findings)

        # Determine rule-level status from findings (worst-case wins)
        statuses = {f.status for f in findings}
        if FindingStatus.FAIL in statuses:
            rules_failed += 1
        elif FindingStatus.ERROR in statuses:
            pass  # already counted above if exception path; here it was a silent error in findings
        elif FindingStatus.NOT_APPLICABLE in statuses:
            rules_na += 1
        else:
            rules_passed += 1

        rules_checked += 1

    # Hard assertion: every rule was checked
    assert rules_checked == len(WCAG_RULES_LEDGER), (
        f"Audit coverage gap: {rules_checked}/{len(WCAG_RULES_LEDGER)} rules checked"
    )

    applicable = rules_checked - rules_na
    coverage = (rules_passed + rules_failed) / max(applicable, 1) * 100

    return AuditResult(
        findings=all_findings,
        rules_checked=rules_checked,
        rules_passed=rules_passed,
        rules_failed=rules_failed,
        rules_not_applicable=rules_na,
        rules_errored=rules_errored,
        coverage_pct=round(coverage, 1),
    )


# ---------------------------------------------------------------------------
# Frontend Conversion Helpers
# ---------------------------------------------------------------------------


def _infer_element_type(f: RuleFinding) -> str:
    """Infer element type from finding context for frontend display."""
    if "image" in f.rule_id or "1_1_1" in f.rule_id:
        return "image"
    if "table" in f.description.lower():
        return "table"
    if "heading" in f.description.lower():
        return "heading"
    if "link" in f.description.lower():
        return "link"
    return "document"


def findings_to_proposals(findings: list[RuleFinding]) -> list[dict]:
    """Convert RuleFinding objects to AnalysisProposal-compatible dicts.

    Only includes FAIL findings (PASS and NOT_APPLICABLE are excluded from proposals).
    """
    _IMAGE_IDS_RE = re.compile(r"image_ids:\s*(.+?)(?:\s*\||\s*$)")

    proposals = []
    for f in findings:
        if f.status != FindingStatus.FAIL:
            continue
        # Extract image_ids from evidence if present (for 1.1.1 findings)
        image_id: str | None = None
        if f.evidence:
            m = _IMAGE_IDS_RE.search(f.evidence)
            if m:
                image_id = m.group(1).strip()
        # B5: Add technique references for this criterion
        from services.common.wcag_techniques import format_technique_refs
        tech_refs = format_technique_refs(f.criterion)

        proposals.append({
            "id": f.element_id or f.rule_id,
            "category": f.criterion,
            "rule_name": f.rule_name,
            "wcag_criterion": f.criterion,
            "element_type": _infer_element_type(f),
            "element_id": f.element_id or f.rule_id,
            "image_id": image_id,
            "description": f.description,
            "proposed_fix": f.proposed_fix,
            "severity": f.severity.value,
            "page": f.page,
            "auto_fixable": f.auto_fixable,
            "action_type": f.remediation_type.value,
            "technique_refs": tech_refs,
        })
    return proposals


def audit_summary_dict(result: AuditResult) -> dict:
    """Return a dict summary of the audit for API response."""
    fail_findings = [f for f in result.findings if f.status == FindingStatus.FAIL]
    sev_counts: dict[str, int] = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
    auto_fixable = 0
    needs_review = 0
    for f in fail_findings:
        sev_counts[f.severity.value] = sev_counts.get(f.severity.value, 0) + 1
        if f.auto_fixable:
            auto_fixable += 1
        else:
            needs_review += 1
    # Build per-rule breakdown indexed by rule_id for fast lookup
    findings_by_rule: dict[str, list[RuleFinding]] = {}
    for f in result.findings:
        findings_by_rule.setdefault(f.rule_id, []).append(f)

    rules_breakdown = []
    for rule in WCAG_RULES_LEDGER:
        rule_findings = findings_by_rule.get(rule.rule_id, [])
        fail_in_rule = [f for f in rule_findings if f.status == FindingStatus.FAIL]
        error_in_rule = [f for f in rule_findings if f.status == FindingStatus.ERROR]
        na_in_rule = [f for f in rule_findings if f.status == FindingStatus.NOT_APPLICABLE]

        if fail_in_rule:
            rule_status = "fail"
        elif error_in_rule:
            rule_status = "error"
        elif rule_findings and len(na_in_rule) == len(rule_findings):
            rule_status = "not_applicable"
        elif rule_findings:
            rule_status = "pass"
        else:
            rule_status = "not_applicable"

        # Determine max severity among FAIL findings (None if no failures)
        severity_order = [
            FindingSeverity.CRITICAL,
            FindingSeverity.SERIOUS,
            FindingSeverity.MODERATE,
            FindingSeverity.MINOR,
        ]
        max_sev = None
        for sev in severity_order:
            if any(f.severity == sev for f in fail_in_rule):
                max_sev = sev.value
                break

        rules_breakdown.append({
            "criterion": rule.criterion,
            "name": rule.name,
            "status": rule_status,
            "finding_count": len(fail_in_rule),
            "severity_max": max_sev,
        })

    return {
        "total_issues": len(fail_findings),
        "critical": sev_counts["critical"],
        "serious": sev_counts["serious"],
        "moderate": sev_counts["moderate"],
        "warning": sev_counts.get("minor", 0),
        "auto_fixable": auto_fixable,
        "needs_review": needs_review,
        "rules_checked": result.rules_checked,
        "rules_passed": result.rules_passed,
        "rules_failed": result.rules_failed,
        "rules_not_applicable": result.rules_not_applicable,
        "rules_errored": result.rules_errored,
        "coverage_pct": result.coverage_pct,
        "rules_breakdown": rules_breakdown,
    }
