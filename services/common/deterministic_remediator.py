"""Deterministic WCAG remediation fixes that require zero AI.

These fixes run BEFORE the AI stage and handle structural WCAG violations
that can be resolved purely by rule-based logic:

- 3.1.1 Language tag: Set lang attribute from PDF metadata or lightweight detection
- 2.4.2 Document title: Set <title> from first heading or filename
- 1.3.1 Table headers: Promote first row to <th scope="col"> when it looks like headers
- 2.4.6 Heading hierarchy: Fix skipped levels (H1->H3 becomes H1->H2->H3)
- 2.4.1 Skip navigation: Insert skip-nav link at top
- 1.3.2 Reading order: Ensure blocks follow document order
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from services.common.ir import (
    BlockType,
    IRBlock,
    IRDocument,
    IRPage,
    RemediationStatus,
)
from services.common.remediation_events import RemediationComponent, RemediationEventCollector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language detection helpers (stdlib only — no external dependencies)
# ---------------------------------------------------------------------------

# Unicode ranges for CJK scripts
_CJK_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs (Chinese/Japanese/Korean)
    (0x3040, 0x309F),   # Hiragana
    (0x30A0, 0x30FF),   # Katakana
    (0xAC00, 0xD7AF),   # Hangul Syllables
    (0x3400, 0x4DBF),   # CJK Extension A
    (0x20000, 0x2A6DF), # CJK Extension B
]

# Distinctive characters for Latin-script languages
_SPANISH_MARKERS = set("áéíóúüñ¡¿ÁÉÍÓÚÜÑ")
_FRENCH_MARKERS = set("àâæçéèêëîïôœùûüÿÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ")
_GERMAN_MARKERS = set("äöüßÄÖÜ")


def _detect_language(text: str) -> tuple[str, bool]:
    """Detect document language from a text sample.

    Uses lightweight heuristics — no external dependencies.
    Returns (language_code, was_detected) where was_detected=False means
    we fell back to "en" without positive evidence.

    Checks (in priority order):
    1. CJK characters → "zh", "ja", or "ko"
    2. German-distinctive umlauts/eszett → "de"
    3. French-distinctive characters (cedilla + accents not in Spanish) → "fr"
    4. Spanish-distinctive ñ or inverted punctuation → "es"
    5. Otherwise → "en" (fallback, was_detected=False)
    """
    if not text:
        return "en", False

    sample = text[:500]

    # --- CJK check ---
    hiragana_count = 0
    katakana_count = 0
    hangul_count = 0
    cjk_count = 0

    for ch in sample:
        cp = ord(ch)
        if 0x3040 <= cp <= 0x309F:
            hiragana_count += 1
        elif 0x30A0 <= cp <= 0x30FF:
            katakana_count += 1
        elif 0xAC00 <= cp <= 0xD7AF:
            hangul_count += 1
        elif (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF):
            cjk_count += 1

    total_cjk = cjk_count + hiragana_count + katakana_count + hangul_count
    if total_cjk >= 3:
        if hangul_count > hiragana_count and hangul_count > katakana_count and hangul_count > cjk_count:
            return "ko", True
        if hiragana_count > 0 or katakana_count > 0:
            return "ja", True
        return "zh", True

    # --- Latin-script language checks ---
    sample_chars = set(sample)

    # German: eszett (ß) is almost exclusively German
    if "ß" in sample_chars or "Ä" in sample_chars or "Ö" in sample_chars or "ü" in sample:
        german_hits = len(sample_chars & _GERMAN_MARKERS)
        if german_hits >= 2:
            return "de", True

    # French: cedilla (ç/Ç) or combinations like œ/æ
    if "ç" in sample_chars or "Ç" in sample_chars or "œ" in sample_chars or "æ" in sample_chars:
        french_hits = len(sample_chars & _FRENCH_MARKERS)
        if french_hits >= 2:
            return "fr", True

    # Spanish: ñ or inverted punctuation
    if "ñ" in sample_chars or "Ñ" in sample_chars or "¿" in sample_chars or "¡" in sample_chars:
        return "es", True

    return "en", False


def apply_deterministic_fixes(
    ir_doc: IRDocument,
    collector: RemediationEventCollector | None = None,
) -> tuple[IRDocument, list[dict]]:
    """Apply all deterministic WCAG fixes to the IR document.

    Returns (modified ir_doc, list of fix event dicts).
    """
    fixes: list[dict] = []

    # 1. Language tag (WCAG 3.1.1)
    fixes.extend(_fix_language_tag(ir_doc, collector))

    # 2. Document title (WCAG 2.4.2)
    fixes.extend(_fix_document_title(ir_doc, collector))

    # 3. Table headers (WCAG 1.3.1)
    fixes.extend(_fix_table_headers(ir_doc, collector))

    # 4. Heading hierarchy (WCAG 2.4.6)
    fixes.extend(_fix_heading_hierarchy(ir_doc, collector))

    # 5. Reading order (WCAG 1.3.2)
    fixes.extend(_fix_reading_order(ir_doc, collector))

    logger.info(
        "deterministic_remediator: applied %d fixes to document %s",
        len(fixes), ir_doc.document_id,
    )
    return ir_doc, fixes


def _fix_language_tag(
    ir_doc: IRDocument,
    collector: RemediationEventCollector | None = None,
) -> list[dict]:
    """WCAG 3.1.1: Ensure document has a language tag.

    Priority order for language resolution:
    1. Existing non-empty ``ir_doc.language`` — already set, no action needed.
    2. PDF metadata field ``pdf_lang`` — authoritative; use as-is.
    3. Lightweight heuristic detection from first 500 chars of text content.
    4. Fallback to "en" with ``language_detected=False`` flag for human review.
    """
    fixes = []

    if ir_doc.language and ir_doc.language.strip():
        # Already has a language tag — nothing to do.
        return fixes

    old = ir_doc.language

    # --- Step 1: check PDF metadata for an explicit /Lang entry ---
    pdf_lang = (
        ir_doc.metadata.get("pdf_lang")
        or ir_doc.metadata.get("lang")
        or ir_doc.metadata.get("language")
    )
    if pdf_lang and isinstance(pdf_lang, str) and pdf_lang.strip():
        detected_lang = pdf_lang.strip()
        was_detected = True
        detection_method = "pdf_metadata"
    else:
        # --- Step 2: lightweight content-based detection ---
        # Gather text from the first 500 characters across all blocks
        text_sample = ""
        for block in ir_doc.all_blocks():
            if block.content:
                text_sample += block.content + " "
            if len(text_sample) >= 500:
                break

        detected_lang, was_detected = _detect_language(text_sample)
        detection_method = "content_heuristic" if was_detected else "fallback_en"

    ir_doc.language = detected_lang

    if not was_detected:
        logger.warning(
            "deterministic_remediator: language_tag could not be detected for"
            " document %s — defaulting to 'en'. Flag for human review.",
            ir_doc.document_id,
        )

    fixes.append({
        "criterion": "3.1.1",
        "fix": "language_tag",
        "before": old or "(none)",
        "after": detected_lang,
        "language_detected": was_detected,
        "detection_method": detection_method,
    })
    if collector:
        collector.record(
            RemediationComponent.LANGUAGE_TAG,
            before=old or "(none)",
            after=detected_lang,
            source="deterministic",
        )

    return fixes


def _fix_document_title(
    ir_doc: IRDocument,
    collector: RemediationEventCollector | None = None,
) -> list[dict]:
    """WCAG 2.4.2: Ensure document has a meaningful title."""
    fixes = []

    current_title = ir_doc.metadata.get("title", "")

    # If title is already set and not a filename, skip
    if current_title and not _is_filename_title(current_title):
        return fixes

    # Try to derive from first heading
    new_title = ""
    for block in ir_doc.all_blocks():
        if block.block_type == BlockType.HEADING and block.content.strip():
            new_title = block.content.strip()
            break

    # Fallback to filename stem
    if not new_title:
        new_title = Path(ir_doc.filename).stem.replace("_", " ").replace("-", " ").title()

    if new_title and new_title != current_title:
        ir_doc.metadata["title"] = new_title
        fixes.append({
            "criterion": "2.4.2",
            "fix": "document_title",
            "before": current_title or "(none)",
            "after": new_title,
        })
        if collector:
            collector.record(
                RemediationComponent.MARK_INFO,
                element_id="document-title",
                before=current_title or "(none)",
                after=new_title,
                source="deterministic",
            )

    return fixes


_FILENAME_PATTERN = re.compile(r"^[\w\-]+\.\w{2,4}$")


def _is_filename_title(title: str) -> bool:
    """Check if title looks like a filename (e.g., 'document.pdf')."""
    return bool(_FILENAME_PATTERN.match(title.strip()))


_NUMERIC_RE = re.compile(r"^\s*[\d,.\-/:%$€£¥]+\s*$")


def _score_header_row(first_row: list, data_rows: list) -> float:
    """Return a confidence score [0.0, 1.0] that *first_row* is a header row.

    Hard veto:
    - If ALL cells in the first row are purely numeric/date patterns, return 0.0
      immediately (numbers/dates = data, not column labels).

    Weighted heuristics (each worth 1.0, total weight 2.0 after the veto):
    - Cells are unique across the row (headers rarely repeat).
    - Cells are shorter on average than data-row cells (labels vs values).

    A score >= 0.6 is treated as "likely a header row" and is promoted.
    """
    if not first_row:
        return 0.0

    cell_strs = [str(c).strip() for c in first_row]

    # --- Hard veto: purely numeric/date first row is never a header row ---
    non_numeric = sum(1 for c in cell_strs if not _NUMERIC_RE.match(c))
    if non_numeric == 0:
        # Every cell is a number or date-like value — definitely data, not headers.
        return 0.0

    score = 0.0
    weight_total = 2.0

    # --- Uniqueness check ---
    if len(set(cell_strs)) == len(cell_strs):
        score += 1.0
    elif len(set(cell_strs)) >= len(cell_strs) * 0.8:
        score += 0.5

    # --- Length check: first row cells shorter than data rows on average ---
    if data_rows:
        first_avg = sum(len(str(c)) for c in first_row) / max(len(first_row), 1)
        data_avgs = []
        for row in data_rows[:5]:  # sample up to 5 data rows
            if isinstance(row, list) and row:
                data_avgs.append(sum(len(str(c)) for c in row) / max(len(row), 1))
        if data_avgs:
            data_avg = sum(data_avgs) / len(data_avgs)
            if data_avg > 0 and first_avg <= data_avg:
                score += 1.0
            elif data_avg > 0 and first_avg <= data_avg * 1.5:
                score += 0.5
        else:
            # No data rows to compare — neutral; give partial credit
            score += 0.5
    else:
        # Single-row table — can't compare; be conservative
        score += 0.5

    return score / weight_total


def _fix_table_headers(
    ir_doc: IRDocument,
    collector: RemediationEventCollector | None = None,
) -> list[dict]:
    """WCAG 1.3.1: Promote first row to <th scope="col"> when no headers exist.

    Uses heuristics to validate that the first row actually looks like headers
    before promoting it. Rows that appear to be data (numeric, non-unique, or
    longer than subsequent rows) are left as-is and skipped for human review.
    """
    fixes = []

    for block in ir_doc.all_blocks():
        if block.block_type != BlockType.TABLE:
            continue

        headers = block.attributes.get("headers", [])
        rows = block.attributes.get("rows", [])

        # Skip if headers already exist or no rows
        if headers or not rows or not isinstance(rows, list):
            continue

        first_row = rows[0]
        if not isinstance(first_row, list) or len(first_row) == 0:
            continue

        data_rows = rows[1:]
        confidence = _score_header_row(first_row, data_rows)

        if confidence >= 0.6:
            block.attributes["headers"] = first_row
            block.attributes["rows"] = data_rows
            block.attributes["header_scope"] = "col"
            block.remediation_status = RemediationStatus.AI_DRAFTED

            fixes.append({
                "criterion": "1.3.1",
                "fix": "table_headers_promoted",
                "element_id": block.block_id,
                "page": block.page_num,
                "headers_added": len(first_row),
                "confidence": round(confidence, 2),
            })
            if collector:
                collector.record(
                    RemediationComponent.TABLE_STRUCTURE,
                    element_id=block.block_id,
                    before="no_headers",
                    after=f"headers_promoted:{len(first_row)}",
                    source="deterministic",
                )
        else:
            # Low confidence — skip promotion; flag for human review
            logger.info(
                "deterministic_remediator: table %s first row does not look like"
                " headers (confidence=%.2f) — skipping promotion; flagging for review.",
                block.block_id, confidence,
            )
            fixes.append({
                "criterion": "1.3.1",
                "fix": "table_headers_skipped_low_confidence",
                "element_id": block.block_id,
                "page": block.page_num,
                "confidence": round(confidence, 2),
                "action": "flagged_for_human_review",
            })

    return fixes


def _parse_heading_level(raw: object) -> int:
    """Coerce a heading level attribute value to an int, defaulting to 1."""
    if isinstance(raw, int):
        return raw
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1


def _fix_heading_hierarchy(
    ir_doc: IRDocument,
    collector: RemediationEventCollector | None = None,
) -> list[dict]:
    """WCAG 2.4.6: Fix skipped heading levels (H1->H3 becomes H1->H2->H3).

    Two passes:
    Pass 1 — If the document's first heading level is > 1, renumber ALL
              headings downward so the sequence starts at H1.  This handles
              documents that begin at H3+ (e.g. H3,H3,H4 → H1,H1,H2).
    Pass 2 — Walk through the (possibly renumbered) headings and fix any
              remaining skipped levels (e.g. H1→H3 → H1→H2).
    """
    fixes = []

    headings = [b for b in ir_doc.all_blocks() if b.block_type == BlockType.HEADING]
    if not headings:
        return fixes

    # --- Pass 1: normalise levels to integers ---
    for h in headings:
        h.attributes["level"] = _parse_heading_level(h.attributes.get("level", 1))

    # --- Pass 2: renumber if first heading > H1 ---
    first_level = headings[0].attributes["level"]
    if first_level > 1:
        offset = first_level - 1  # amount to subtract from every heading
        for h in headings:
            old_level = h.attributes["level"]
            new_level = max(1, old_level - offset)
            h.attributes["level"] = new_level
            h.remediation_status = RemediationStatus.AI_DRAFTED

            fixes.append({
                "criterion": "2.4.6",
                "fix": "heading_level_adjusted",
                "element_id": h.block_id,
                "page": h.page_num,
                "before": f"h{old_level}",
                "after": f"h{new_level}",
                "reason": "start_level_normalised",
            })
            if collector:
                collector.record(
                    RemediationComponent.HEADING_HIERARCHY,
                    element_id=h.block_id,
                    before=f"h{old_level}",
                    after=f"h{new_level}",
                    source="deterministic",
                )

    # --- Pass 3: fix any remaining skipped levels ---
    prev_level = 0
    for h in headings:
        level = h.attributes["level"]

        if prev_level > 0 and level > prev_level + 1:
            new_level = prev_level + 1
            old_level = level
            h.attributes["level"] = new_level
            h.remediation_status = RemediationStatus.AI_DRAFTED

            fixes.append({
                "criterion": "2.4.6",
                "fix": "heading_level_adjusted",
                "element_id": h.block_id,
                "page": h.page_num,
                "before": f"h{old_level}",
                "after": f"h{new_level}",
                "reason": "skipped_level_fixed",
            })
            if collector:
                collector.record(
                    RemediationComponent.HEADING_HIERARCHY,
                    element_id=h.block_id,
                    before=f"h{old_level}",
                    after=f"h{new_level}",
                    source="deterministic",
                )
            level = new_level

        prev_level = level

    return fixes


def _fix_reading_order(
    ir_doc: IRDocument,
    collector: RemediationEventCollector | None = None,
) -> list[dict]:
    """WCAG 1.3.2: Ensure blocks follow top-to-bottom reading order within each page.

    Sorts blocks by y1 coordinate (top of bounding box), then x1 for same-line elements.
    Only re-orders if the current order differs from spatial order.
    """
    fixes = []

    for page in ir_doc.pages:
        blocks = page.blocks
        if len(blocks) < 2:
            continue

        # Check if any blocks have valid bounding boxes
        has_bboxes = any(b.bbox.y1 > 0 or b.bbox.x1 > 0 for b in blocks)
        if not has_bboxes:
            continue

        # Sort by y1 (top-to-bottom), then x1 (left-to-right) for same line.
        # Round y1 to the nearest 5 units (was 10) to reduce false merging of
        # blocks in narrow multi-column layouts.  Two blocks must be within 5
        # PDF points of each other vertically to be treated as the same line.
        sorted_blocks = sorted(
            blocks,
            key=lambda b: (round(b.bbox.y1 / 5) * 5, b.bbox.x1),
        )

        # Check if order changed
        original_ids = [b.block_id for b in blocks]
        sorted_ids = [b.block_id for b in sorted_blocks]

        if original_ids != sorted_ids:
            page.blocks = sorted_blocks
            reordered = sum(1 for a, b in zip(original_ids, sorted_ids) if a != b)
            fixes.append({
                "criterion": "1.3.2",
                "fix": "reading_order_corrected",
                "page": page.page_num,
                "blocks_reordered": reordered,
            })
            if collector:
                collector.record(
                    RemediationComponent.TAB_ORDER,
                    element_id=f"page-{page.page_num}",
                    before=f"unordered:{reordered}",
                    after="spatial_order",
                    source="deterministic",
                )

    return fixes
