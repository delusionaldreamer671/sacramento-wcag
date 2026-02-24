"""Post-process Adobe Auto-Tagged PDFs with pipeline-derived enhancements.

After Adobe Auto-Tag produces a tagged PDF, this module uses pikepdf to:
  1. Inject AI-generated alt text into /Figure structure elements (/Alt attribute)
  2. Set /Lang on the document catalog (WCAG 3.1.1)
  3. Generate bookmarks (outlines) from the IR heading structure (WCAG 2.4.5)
  4. Verify /MarkInfo is present (required for PDF/UA)

The tagged PDF from Auto-Tag already has:
  - /StructTreeRoot with tagged content (headings, paragraphs, tables, figures)
  - /MarkInfo dictionary
  - Reading order from the original document layout

This module only *enhances* — it never removes or replaces existing tags.
"""

from __future__ import annotations

import logging
from typing import Any

from services.common.ir import BlockType, IRBlock, IRDocument

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# pikepdf import — guarded for graceful degradation
# ---------------------------------------------------------------------------

try:
    import pikepdf  # noqa: F401
    _PIKEPDF_AVAILABLE = True
except ImportError:
    _PIKEPDF_AVAILABLE = False
    logger.warning(
        "pikepdf not installed. PDF tag enhancement will be unavailable. "
        "Install with: pip install pikepdf"
    )


# ---------------------------------------------------------------------------
# Clause fixer helper (PyMuPDF final pass)
# ---------------------------------------------------------------------------


def _apply_clause_fixers(pdf_bytes: bytes, collector: Any | None = None) -> bytes:
    """Apply PDF/UA-1 clause fixers using PyMuPDF xref-level manipulation."""
    try:
        from services.recompilation.clause_fixers import ClauseFixerPipeline
    except ImportError:
        logger.debug("clause_fixers not importable (PyMuPDF missing?) — skipping")
        return pdf_bytes

    try:
        # Try to get VeraPDF client for accept/reject logic
        verapdf = None
        try:
            from services.common.verapdf_client import VeraPDFClient
            client = VeraPDFClient()
            if client.is_available():
                verapdf = client
        except ImportError:
            pass

        pipeline = ClauseFixerPipeline(
            verapdf_client=verapdf,
            collector=collector,
        )
        fixed_bytes, results = pipeline.apply_all(pdf_bytes)

        applied = [r for r in results if r.applied]
        errors = [r for r in results if r.error]
        if applied:
            logger.info(
                "Clause fixers: %d/%d applied (%s)",
                len(applied), len(results),
                ", ".join(f"clause {r.clause}" for r in applied),
            )
        if errors:
            logger.warning(
                "Clause fixers: %d errors (%s)",
                len(errors),
                ", ".join(f"clause {r.clause}: {r.error}" for r in errors),
            )

        return fixed_bytes
    except Exception as exc:
        logger.warning("Clause fixer pipeline failed (%s) — returning original", exc)
        return pdf_bytes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enhance_tagged_pdf(
    tagged_pdf_bytes: bytes,
    ir_doc: IRDocument,
    collector: Any | None = None,
) -> bytes:
    """Enhance an Adobe Auto-Tagged PDF with pipeline-derived data.

    Applies all enhancements in sequence:
      1. Set /Lang on document catalog
      2. Ensure /MarkInfo is present
      3. Inject alt text into /Figure structure elements
      4. Generate bookmarks from IR headings

    Args:
        tagged_pdf_bytes: Raw bytes of the Auto-Tagged PDF from Adobe.
        ir_doc: The IRDocument with AI-generated alt text, heading levels, etc.
        collector: Optional remediation event collector for audit trail.

    Returns:
        Enhanced PDF bytes. Returns the original bytes unchanged if pikepdf
        is unavailable or any error occurs.
    """
    if not _PIKEPDF_AVAILABLE:
        logger.warning("enhance_tagged_pdf: pikepdf not available — returning original")
        return tagged_pdf_bytes

    if not tagged_pdf_bytes:
        logger.warning("enhance_tagged_pdf: empty tagged_pdf_bytes — nothing to enhance")
        return tagged_pdf_bytes

    import pikepdf  # noqa: PLC0415

    try:
        pdf = _open_from_bytes(tagged_pdf_bytes)
    except Exception as exc:
        logger.error("enhance_tagged_pdf: failed to open tagged PDF: %s", exc)
        return tagged_pdf_bytes

    try:
        changes = 0

        # 1. Set /Lang on document catalog (WCAG 3.1.1)
        lang_changes = _set_language(pdf, ir_doc.language)
        changes += lang_changes
        if collector and lang_changes:
            from services.common.remediation_events import RemediationComponent  # noqa: PLC0415
            collector.record(RemediationComponent.LANGUAGE_TAG, after=ir_doc.language)

        # 2. Ensure /MarkInfo is present
        mark_changes = _ensure_mark_info(pdf)
        changes += mark_changes
        if collector and mark_changes:
            from services.common.remediation_events import RemediationComponent  # noqa: PLC0415
            collector.record(RemediationComponent.MARK_INFO, after="Marked=true")

        # 3. Inject alt text into /Figure structure elements
        alt_texts = _collect_alt_texts(ir_doc)
        alt_changes = _inject_alt_text(pdf, alt_texts)
        changes += alt_changes
        if collector and alt_texts:
            from services.common.remediation_events import RemediationComponent  # noqa: PLC0415
            for i, alt in enumerate(alt_texts):
                if alt:
                    collector.record(
                        RemediationComponent.ALT_TEXT,
                        element_id=f"figure-{i}",
                        before=None,
                        after=alt,
                        source="pipeline",
                    )

        # 4. Generate bookmarks from IR headings
        headings = _collect_headings(ir_doc)
        changes += _generate_bookmarks(pdf, headings)

        logger.info(
            "enhance_tagged_pdf: %d enhancements applied to %s",
            changes, ir_doc.filename,
        )

        # Save to bytes
        import io  # noqa: PLC0415
        buf = io.BytesIO()
        pdf.save(buf)
        pdf.close()
        enhanced_bytes = buf.getvalue()

        # Post-build verification: confirm PDF/UA markers survived enhancement
        ok, missing = verify_pdf_ua_markers(enhanced_bytes)
        if not ok:
            logger.warning(
                "enhance_tagged_pdf: post-build verification failed — "
                "missing markers: %s. Returning original bytes.",
                ", ".join(missing),
            )
            return tagged_pdf_bytes

        # Apply PyMuPDF clause fixers as final pass
        enhanced_bytes = _apply_clause_fixers(enhanced_bytes, collector)

        return enhanced_bytes

    except Exception as exc:
        logger.error("enhance_tagged_pdf: error during enhancement: %s", exc)
        try:
            pdf.close()
        except Exception:
            pass
        return tagged_pdf_bytes


# ---------------------------------------------------------------------------
# Post-build verification
# ---------------------------------------------------------------------------


def verify_pdf_ua_markers(pdf_bytes: bytes) -> tuple[bool, list[str]]:
    """Verify that essential PDF/UA structural markers are present.

    Checks for three required markers:
      1. /StructTreeRoot — the tag tree (PDF/UA core requirement)
      2. /MarkInfo with /Marked=true — indicates tagged PDF
      3. /Lang — document language (WCAG 3.1.1)

    Args:
        pdf_bytes: Raw bytes of the PDF to verify.

    Returns:
        (passed, missing_markers): True if all markers present,
        plus a list of any missing marker names.
    """
    if not _PIKEPDF_AVAILABLE:
        return True, []  # Cannot verify without pikepdf — assume OK

    if not pdf_bytes:
        return False, ["/StructTreeRoot", "/MarkInfo", "/Lang"]

    import pikepdf  # noqa: PLC0415

    try:
        pdf = _open_from_bytes(pdf_bytes)
    except Exception as exc:
        logger.error("verify_pdf_ua_markers: failed to open PDF: %s", exc)
        return False, ["unable to open PDF"]

    missing: list[str] = []

    try:
        # 1. /StructTreeRoot
        if pdf.Root.get("/StructTreeRoot") is None:
            missing.append("/StructTreeRoot")

        # 2. /MarkInfo with /Marked=true
        mark_info = pdf.Root.get("/MarkInfo")
        if mark_info is None:
            missing.append("/MarkInfo")
        else:
            marked = mark_info.get("/Marked")
            if marked is None or not bool(marked):
                missing.append("/MarkInfo./Marked=true")

        # 3. /Lang
        if pdf.Root.get("/Lang") is None:
            missing.append("/Lang")

    finally:
        pdf.close()

    passed = len(missing) == 0
    if passed:
        logger.debug("verify_pdf_ua_markers: all 3 markers present")
    else:
        logger.warning("verify_pdf_ua_markers: missing %s", missing)

    return passed, missing


# ---------------------------------------------------------------------------
# Internal: open PDF from bytes
# ---------------------------------------------------------------------------


def _open_from_bytes(pdf_bytes: bytes) -> "pikepdf.Pdf":
    """Open a pikepdf.Pdf from raw bytes."""
    import io  # noqa: PLC0415
    import pikepdf  # noqa: PLC0415
    return pikepdf.open(io.BytesIO(pdf_bytes))


# ---------------------------------------------------------------------------
# Enhancement 1: Language tag
# ---------------------------------------------------------------------------


def _set_language(pdf: "pikepdf.Pdf", language: str) -> int:
    """Set /Lang on the document catalog. Returns 1 if changed, 0 if already set."""
    import pikepdf  # noqa: PLC0415

    lang = language.strip() or "en"
    current = pdf.Root.get("/Lang")

    if current is not None and str(current) == lang:
        logger.debug("_set_language: /Lang already set to '%s'", lang)
        return 0

    pdf.Root["/Lang"] = pikepdf.String(lang)
    logger.info("_set_language: set /Lang='%s' on document catalog", lang)
    return 1


# ---------------------------------------------------------------------------
# Enhancement 2: MarkInfo
# ---------------------------------------------------------------------------


def _ensure_mark_info(pdf: "pikepdf.Pdf") -> int:
    """Ensure /MarkInfo with /Marked=true exists in the catalog.

    Auto-Tagged PDFs should already have this, but we verify as a safety net.
    Returns 1 if added, 0 if already present.
    """
    import pikepdf  # noqa: PLC0415

    mark_info = pdf.Root.get("/MarkInfo")
    if mark_info is not None:
        # Verify /Marked is true
        marked = mark_info.get("/Marked")
        if marked is not None and bool(marked):
            return 0
        mark_info["/Marked"] = pikepdf.Boolean(True)
        logger.info("_ensure_mark_info: set /Marked=true on existing /MarkInfo")
        return 1

    pdf.Root["/MarkInfo"] = pikepdf.Dictionary({"/Marked": pikepdf.Boolean(True)})
    logger.info("_ensure_mark_info: created /MarkInfo with /Marked=true")
    return 1


# ---------------------------------------------------------------------------
# Enhancement 3: Alt text injection
# ---------------------------------------------------------------------------


def _collect_alt_texts(ir_doc: IRDocument) -> list[str]:
    """Collect alt text strings from IMAGE blocks in document order.

    Returns a list of alt text strings in the order images appear in the IR.
    Empty strings for images without alt text (decorative).
    """
    alt_texts: list[str] = []
    for block in ir_doc.all_blocks():
        if block.block_type == BlockType.IMAGE:
            alt = block.attributes.get("alt", "")
            alt_texts.append(alt)
    return alt_texts


def _inject_alt_text(
    pdf: "pikepdf.Pdf",
    alt_texts: list[str],
) -> int:
    """Walk the PDF structure tree and inject /Alt on /Figure elements.

    Matches figures in document order: the Nth /Figure in the structure tree
    gets the Nth alt text from our IR. This relies on Adobe Auto-Tag producing
    figures in the same reading order as our extraction pipeline.

    Args:
        pdf: An open pikepdf.Pdf object.
        alt_texts: Alt text strings in document order (from _collect_alt_texts).

    Returns:
        Number of alt text values injected.
    """
    import pikepdf  # noqa: PLC0415

    struct_tree = pdf.Root.get("/StructTreeRoot")
    if struct_tree is None:
        logger.warning("_inject_alt_text: no /StructTreeRoot — cannot inject alt text")
        return 0

    # Walk the structure tree to find /Figure elements
    figures: list[pikepdf.Dictionary] = []
    _find_struct_elements(struct_tree, "/Figure", figures)

    if not figures:
        logger.debug("_inject_alt_text: no /Figure elements found in structure tree")
        return 0

    injected = 0
    for idx, figure_elem in enumerate(figures):
        if idx >= len(alt_texts):
            break

        alt = alt_texts[idx]
        if not alt:
            continue

        # Only inject if /Alt is not already set or is empty
        existing_alt = figure_elem.get("/Alt")
        if existing_alt is not None and str(existing_alt).strip():
            logger.debug(
                "_inject_alt_text: figure %d already has /Alt='%s' — skipping",
                idx, str(existing_alt)[:50],
            )
            continue

        figure_elem["/Alt"] = pikepdf.String(alt)
        injected += 1

    logger.info(
        "_inject_alt_text: injected alt text on %d/%d figures (%d in IR)",
        injected, len(figures), len(alt_texts),
    )
    return injected


def _find_struct_elements(
    node: Any,
    target_type: str,
    results: list,
) -> None:
    """Recursively find structure elements of a given type in the PDF structure tree.

    Args:
        node: A pikepdf object (Dictionary, Array, or other).
        target_type: The /S value to match (e.g. "/Figure", "/H1").
        results: Accumulator list — matched elements are appended here.
    """
    import pikepdf  # noqa: PLC0415

    if not isinstance(node, (pikepdf.Dictionary, pikepdf.Array)):
        return

    if isinstance(node, pikepdf.Dictionary):
        # Check if this node is a structure element of the target type
        s_type = node.get("/S")
        if s_type is not None and str(s_type) == target_type:
            results.append(node)

        # Recurse into /K (kids) — can be a single element or an array
        kids = node.get("/K")
        if kids is not None:
            _find_struct_elements(kids, target_type, results)

    elif isinstance(node, pikepdf.Array):
        for child in node:
            _find_struct_elements(child, target_type, results)


# ---------------------------------------------------------------------------
# Enhancement 4: Bookmarks from headings
# ---------------------------------------------------------------------------


def _collect_headings(ir_doc: IRDocument) -> list[tuple[int, str]]:
    """Collect (level, text) tuples from HEADING blocks in document order."""
    headings: list[tuple[int, str]] = []
    for block in ir_doc.all_blocks():
        if block.block_type == BlockType.HEADING:
            level = block.attributes.get("level", 2)
            text = block.content.strip()
            if text:
                headings.append((level, text))
    return headings


def _generate_bookmarks(
    pdf: "pikepdf.Pdf",
    headings: list[tuple[int, str]],
) -> int:
    """Generate PDF outline (bookmarks) from IR heading structure.

    Creates a flat outline with all headings as top-level entries pointing
    to page 1 (since we don't have page-level mapping for individual headings
    in the current IR). This is a POC approach — a production version would
    match headings to specific pages via the structure tree.

    Args:
        pdf: An open pikepdf.Pdf object.
        headings: List of (level, text) tuples from _collect_headings.

    Returns:
        Number of bookmarks created.
    """
    if not headings:
        return 0

    # Skip if bookmarks already exist (Auto-Tag may have created them)
    existing_outlines = pdf.Root.get("/Outlines")
    if existing_outlines is not None:
        # Check if it has children
        count = existing_outlines.get("/Count")
        if count is not None and int(count) > 0:
            logger.debug(
                "_generate_bookmarks: PDF already has %d bookmarks — skipping",
                int(count),
            )
            return 0

    import pikepdf  # noqa: PLC0415

    if len(pdf.pages) == 0:
        return 0

    # Build a nested bookmark structure respecting heading levels.
    # H1 → top-level, H2 → child of last H1, H3 → child of last H2, etc.
    # For the POC, all bookmarks point to page 0 (first page).
    # A production version would map headings to actual pages.

    with pdf.open_outline() as outline:
        # Stack tracks (level, outline_item) for nesting
        stack: list[tuple[int, Any]] = []

        for level, text in headings:
            item = pikepdf.OutlineItem(text, 0)  # Page 0 destination

            if not stack:
                outline.root.append(item)
                stack.append((level, item))
                continue

            # Pop stack until we find a parent at a higher (lower number) level
            while stack and stack[-1][0] >= level:
                stack.pop()

            if stack:
                # Add as child of the current parent
                parent_item = stack[-1][1]
                parent_item.children.append(item)
            else:
                # Top-level bookmark
                outline.root.append(item)

            stack.append((level, item))

    created = len(headings)
    logger.info("_generate_bookmarks: created %d bookmarks from headings", created)
    return created
