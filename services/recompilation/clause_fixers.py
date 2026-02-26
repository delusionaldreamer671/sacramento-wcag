"""PDF/UA-1 clause fixers using PyMuPDF (fitz) xref-level manipulation.

These fixers run AFTER Adobe Auto-Tag + pikepdf enhancement as a final pass.
They address specific PDF/UA-1 clauses that Adobe Auto-Tag does not fix.

CRITICAL: Do NOT add StructTreeRoot (clause 7.1.3/7.1.11) to untagged PDFs
— this causes 425+ VeraPDF regressions.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    _FITZ_AVAILABLE = True
except ImportError:
    _FITZ_AVAILABLE = False
    logger.debug("PyMuPDF (fitz) not installed — clause fixers unavailable")


class ClauseFixResult(BaseModel):
    """Result of applying a single clause fixer."""
    clause: str
    description: str
    applied: bool = False
    before_state: str = ""
    after_state: str = ""
    error: str | None = None


def fix_pdfuaid(pdf_bytes: bytes) -> tuple[bytes, ClauseFixResult]:
    """Clause 5: Inject pdfuaid:part=1 into XMP metadata."""
    result = ClauseFixResult(
        clause="5",
        description="PDF/UA identifier in XMP metadata",
    )
    if not _FITZ_AVAILABLE:
        result.error = "PyMuPDF not available"
        return pdf_bytes, result

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        # Check existing XMP metadata
        xmp = doc.get_xml_metadata()
        result.before_state = "pdfuaid:part present" if "pdfuaid:part" in xmp else "no pdfuaid"

        if "pdfuaid:part" in xmp:
            result.applied = False
            result.after_state = result.before_state
            doc.close()
            return pdf_bytes, result

        # Inject pdfuaid:part=1 into XMP
        # Find the closing </rdf:Description> or </rdf:RDF> and inject before it
        pdfua_ns = 'xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/"'
        pdfua_part = "<pdfuaid:part>1</pdfuaid:part>"

        if xmp and "</rdf:Description>" in xmp:
            # Add namespace to rdf:Description if not present
            if "pdfuaid" not in xmp:
                xmp = xmp.replace(
                    "<rdf:Description",
                    f"<rdf:Description {pdfua_ns}",
                    1,
                )
            xmp = xmp.replace(
                "</rdf:Description>",
                f"  {pdfua_part}\n  </rdf:Description>",
                1,
            )
        elif xmp:
            # Minimal XMP — append before closing rdf:RDF
            if "</rdf:RDF>" in xmp:
                insert = (
                    f'<rdf:Description rdf:about="" {pdfua_ns}>\n'
                    f'  {pdfua_part}\n'
                    f'</rdf:Description>\n'
                )
                xmp = xmp.replace("</rdf:RDF>", insert + "</rdf:RDF>")
        else:
            # No XMP at all — create minimal
            xmp = (
                '<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
                '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
                '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
                f'<rdf:Description rdf:about="" {pdfua_ns}>\n'
                f'  {pdfua_part}\n'
                '</rdf:Description>\n'
                '</rdf:RDF>\n'
                '</x:xmpmeta>\n'
                '<?xpacket end="w"?>'
            )

        doc.set_xml_metadata(xmp)
        out = doc.tobytes(deflate=True, garbage=3)
        doc.close()

        result.applied = True
        result.after_state = "pdfuaid:part=1 injected"
        return out, result

    except Exception as exc:
        result.error = str(exc)
        logger.warning("fix_pdfuaid failed: %s", exc)
        return pdf_bytes, result


def fix_markinfo(pdf_bytes: bytes) -> tuple[bytes, ClauseFixResult]:
    """Clause 6.2.1: Set /MarkInfo <</Marked true>> on catalog."""
    result = ClauseFixResult(
        clause="6.2.1",
        description="MarkInfo Marked=true on catalog",
    )
    if not _FITZ_AVAILABLE:
        result.error = "PyMuPDF not available"
        return pdf_bytes, result

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        cat_xref = doc.pdf_catalog()

        # Check current state
        markinfo = doc.xref_get_key(cat_xref, "MarkInfo")
        result.before_state = f"MarkInfo={markinfo[1]}" if markinfo[0] != "null" else "no MarkInfo"

        if markinfo[0] != "null" and "true" in markinfo[1].lower():
            result.applied = False
            result.after_state = result.before_state
            doc.close()
            return pdf_bytes, result

        # Set /MarkInfo <</Marked true>>
        doc.xref_set_key(cat_xref, "MarkInfo", "<</Marked true>>")

        out = doc.tobytes(deflate=True, garbage=3)
        doc.close()

        result.applied = True
        result.after_state = "MarkInfo <</Marked true>>"
        return out, result

    except Exception as exc:
        result.error = str(exc)
        logger.warning("fix_markinfo failed: %s", exc)
        return pdf_bytes, result


def fix_displaydoctitle(pdf_bytes: bytes) -> tuple[bytes, ClauseFixResult]:
    """Clause 7.1.10: Set /ViewerPreferences <</DisplayDocTitle true>>."""
    result = ClauseFixResult(
        clause="7.1.10",
        description="ViewerPreferences DisplayDocTitle=true",
    )
    if not _FITZ_AVAILABLE:
        result.error = "PyMuPDF not available"
        return pdf_bytes, result

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        cat_xref = doc.pdf_catalog()

        vp = doc.xref_get_key(cat_xref, "ViewerPreferences")
        result.before_state = f"ViewerPreferences={vp[1]}" if vp[0] != "null" else "no ViewerPreferences"

        if vp[0] != "null" and "DisplayDocTitle" in vp[1] and "true" in vp[1].lower():
            result.applied = False
            result.after_state = result.before_state
            doc.close()
            return pdf_bytes, result

        # Set or update ViewerPreferences
        doc.xref_set_key(cat_xref, "ViewerPreferences", "<</DisplayDocTitle true>>")

        out = doc.tobytes(deflate=True, garbage=3)
        doc.close()

        result.applied = True
        result.after_state = "ViewerPreferences <</DisplayDocTitle true>>"
        return out, result

    except Exception as exc:
        result.error = str(exc)
        logger.warning("fix_displaydoctitle failed: %s", exc)
        return pdf_bytes, result


def fix_tabs_s(pdf_bytes: bytes) -> tuple[bytes, ClauseFixResult]:
    """Clause 7.18.3: Set /Tabs /S on all page dictionaries."""
    result = ClauseFixResult(
        clause="7.18.3",
        description="Page Tabs=S (structure order for tab navigation)",
    )
    if not _FITZ_AVAILABLE:
        result.error = "PyMuPDF not available"
        return pdf_bytes, result

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages_fixed = 0
        pages_total = len(doc)

        for page_num in range(pages_total):
            page = doc[page_num]
            xref = page.xref
            tabs = doc.xref_get_key(xref, "Tabs")
            if tabs[0] == "null" or tabs[1] != "/S":
                doc.xref_set_key(xref, "Tabs", "/S")
                pages_fixed += 1

        result.before_state = f"{pages_total - pages_fixed}/{pages_total} pages had /Tabs /S"

        if pages_fixed == 0:
            result.applied = False
            result.after_state = result.before_state
            doc.close()
            return pdf_bytes, result

        out = doc.tobytes(deflate=True, garbage=3)
        doc.close()

        result.applied = True
        result.after_state = f"All {pages_total} pages now have /Tabs /S ({pages_fixed} fixed)"
        return out, result

    except Exception as exc:
        result.error = str(exc)
        logger.warning("fix_tabs_s failed: %s", exc)
        return pdf_bytes, result


def fix_cidset(pdf_bytes: bytes) -> tuple[bytes, ClauseFixResult]:
    """Clause 7.21.4.2: Remove incomplete /CIDSet entries from font descriptors.

    /CIDSet must be a complete bitmap of all CIDs used. Incomplete entries
    cause VeraPDF failures. Safest fix: remove /CIDSet entirely (PDF 2.0
    deprecated it; VeraPDF accepts absence).
    """
    result = ClauseFixResult(
        clause="7.21.4.2",
        description="Remove incomplete CIDSet from font descriptors",
    )
    if not _FITZ_AVAILABLE:
        result.error = "PyMuPDF not available"
        return pdf_bytes, result

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        removed = 0

        # Walk all xrefs looking for font descriptors with /CIDSet
        xref_count = doc.xref_length()
        for xref in range(1, xref_count):
            try:
                # Check if this xref is a font descriptor
                obj_type = doc.xref_get_key(xref, "Type")
                if obj_type[0] != "null" and "FontDescriptor" in obj_type[1]:
                    cidset = doc.xref_get_key(xref, "CIDSet")
                    if cidset[0] != "null":
                        doc.xref_set_key(xref, "CIDSet", "null")
                        removed += 1
            except Exception as exc:
                logger.warning("fix_cidset: skipping xref %d due to error: %s", xref, exc)
                continue

        result.before_state = f"{removed} font descriptors had /CIDSet"

        if removed == 0:
            result.applied = False
            result.after_state = "No /CIDSet entries found"
            doc.close()
            return pdf_bytes, result

        out = doc.tobytes(deflate=True, garbage=3)
        doc.close()

        result.applied = True
        result.after_state = f"Removed /CIDSet from {removed} font descriptors"
        return out, result

    except Exception as exc:
        result.error = str(exc)
        logger.warning("fix_cidset failed: %s", exc)
        return pdf_bytes, result


class ClauseFixerPipeline:
    """Orchestrates all five clause fixers with optional accept/reject logic."""

    # Ordered list of fixers (each returns tuple[bytes, ClauseFixResult])
    FIXERS = [
        fix_pdfuaid,
        fix_markinfo,
        fix_displaydoctitle,
        fix_tabs_s,
        fix_cidset,
    ]

    def __init__(
        self,
        verapdf_client: Any | None = None,
        collector: Any | None = None,
    ) -> None:
        self._verapdf = verapdf_client
        self._collector = collector

    def apply_all(
        self,
        pdf_bytes: bytes,
        baseline_verapdf: Any | None = None,
    ) -> tuple[bytes, list[ClauseFixResult]]:
        """Apply all clause fixers to pdf_bytes.

        When VeraPDF is available: validate after each fix, reject if errors
        increase. When unavailable: apply all unconditionally (safe metadata).
        """
        if not _FITZ_AVAILABLE:
            return pdf_bytes, [
                ClauseFixResult(
                    clause="all", description="PyMuPDF not available",
                    error="PyMuPDF (fitz) not installed",
                )
            ]

        results: list[ClauseFixResult] = []
        current_bytes = pdf_bytes

        # Get baseline error count for accept/reject
        baseline_errors = None
        if self._verapdf and baseline_verapdf:
            baseline_errors = baseline_verapdf.error_count
        elif self._verapdf:
            try:
                pre_result = self._verapdf.validate_pdfua1(current_bytes)
                if pre_result:
                    baseline_errors = pre_result.error_count
            except Exception:
                pass

        for fixer in self.FIXERS:
            try:
                fixed_bytes, fix_result = fixer(current_bytes)
            except Exception as exc:
                results.append(ClauseFixResult(
                    clause=fixer.__name__,
                    description=f"Unexpected error: {exc}",
                    error=str(exc),
                ))
                continue

            if not fix_result.applied:
                results.append(fix_result)
                continue

            # Accept/reject with VeraPDF when available
            if self._verapdf and baseline_errors is not None:
                try:
                    post_result = self._verapdf.validate_pdfua1(fixed_bytes)
                    if post_result and post_result.error_count > baseline_errors:
                        fix_result.applied = False
                        fix_result.error = (
                            f"Rejected: errors increased from {baseline_errors} "
                            f"to {post_result.error_count}"
                        )
                        results.append(fix_result)
                        continue
                    # Update baseline for next fixer
                    if post_result:
                        baseline_errors = post_result.error_count
                except Exception as verapdf_exc:
                    # Reject the fix when validation is broken — accepting
                    # unvalidated fixes is a fail-open anti-pattern.
                    logger.warning(
                        "VeraPDF validation error during fix acceptance check: %s — "
                        "rejecting fix to prevent unvalidated changes", verapdf_exc,
                    )
                    fix_result.applied = False
                    fix_result.error = (
                        f"Rejected: VeraPDF validation failed ({verapdf_exc}). "
                        "Cannot confirm fix doesn't increase errors."
                    )
                    results.append(fix_result)
                    continue

            # Accept the fix
            current_bytes = fixed_bytes
            results.append(fix_result)

            # Record to collector
            if self._collector:
                try:
                    from services.common.remediation_events import RemediationComponent
                    component_map = {
                        "5": RemediationComponent.PDFUA_METADATA,
                        "6.2.1": RemediationComponent.MARK_INFO,
                        "7.1.10": RemediationComponent.VIEWER_PREFERENCES,
                        "7.18.3": RemediationComponent.TAB_ORDER,
                        "7.21.4.2": RemediationComponent.CIDSET_REMOVAL,
                    }
                    comp = component_map.get(fix_result.clause)
                    if comp:
                        self._collector.record(
                            comp,
                            before=fix_result.before_state,
                            after=fix_result.after_state,
                            source="clause_fixer",
                        )
                except Exception:
                    pass

        return current_bytes, results
