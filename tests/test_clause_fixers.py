"""Tests for services/recompilation/clause_fixers.py.

Covers all 5 clause fixers, ClauseFixerPipeline, ClauseFixResult, and the
_apply_clause_fixers integration helper. Uses fitz/PyMuPDF to create minimal
valid PDFs for testing.

CRITICAL safety test: verify StructTreeRoot is NOT added by the pipeline
(would cause 425+ VeraPDF regressions — see module docstring).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# fitz (PyMuPDF) availability guard
# All fitz imports inside test bodies are local (import fitz) so that pytest
# collection succeeds even when PyMuPDF is not installed.
# ---------------------------------------------------------------------------

try:
    import fitz as _fitz_probe  # noqa: F401
    _FITZ = True
except ImportError:
    _FITZ = False

skip_no_fitz = pytest.mark.skipif(not _FITZ, reason="PyMuPDF (fitz) not installed")


# ---------------------------------------------------------------------------
# Helper: create minimal valid PDF bytes using fitz.
# All helpers do a local `import fitz` so they are safe to define at module
# level — they only run from @skip_no_fitz-decorated tests.
# ---------------------------------------------------------------------------


def _make_minimal_pdf(num_pages: int = 1) -> bytes:
    """Create a minimal valid PDF with the given number of blank pages."""
    import fitz  # noqa: PLC0415
    doc = fitz.open()
    for _ in range(num_pages):
        doc.new_page()
    pdf_bytes = doc.tobytes(deflate=True)
    doc.close()
    return pdf_bytes


def _make_pdf_with_pdfuaid() -> bytes:
    """Create a PDF that already contains pdfuaid:part in its XMP metadata."""
    import fitz  # noqa: PLC0415
    doc = fitz.open()
    doc.new_page()
    xmp = (
        '<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '<rdf:Description rdf:about="" '
        'xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/">\n'
        "  <pdfuaid:part>1</pdfuaid:part>\n"
        "</rdf:Description>\n"
        "</rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    )
    doc.set_xml_metadata(xmp)
    pdf_bytes = doc.tobytes(deflate=True)
    doc.close()
    return pdf_bytes


def _make_pdf_with_markinfo() -> bytes:
    """Create a PDF that already has MarkInfo Marked=true on the catalog."""
    import fitz  # noqa: PLC0415
    doc = fitz.open()
    doc.new_page()
    cat_xref = doc.pdf_catalog()
    doc.xref_set_key(cat_xref, "MarkInfo", "<</Marked true>>")
    pdf_bytes = doc.tobytes(deflate=True)
    doc.close()
    return pdf_bytes


def _make_pdf_with_displaydoctitle() -> bytes:
    """Create a PDF that already has ViewerPreferences DisplayDocTitle=true."""
    import fitz  # noqa: PLC0415
    doc = fitz.open()
    doc.new_page()
    cat_xref = doc.pdf_catalog()
    doc.xref_set_key(cat_xref, "ViewerPreferences", "<</DisplayDocTitle true>>")
    pdf_bytes = doc.tobytes(deflate=True)
    doc.close()
    return pdf_bytes


def _make_pdf_with_tabs_s_all_pages(num_pages: int = 2) -> bytes:
    """Create a PDF where all pages already have /Tabs /S."""
    import fitz  # noqa: PLC0415
    doc = fitz.open()
    for _ in range(num_pages):
        page = doc.new_page()
        doc.xref_set_key(page.xref, "Tabs", "/S")
    pdf_bytes = doc.tobytes(deflate=True)
    doc.close()
    return pdf_bytes


# ---------------------------------------------------------------------------
# ClauseFixResult model tests — no fitz required
# ---------------------------------------------------------------------------


def test_clause_fix_result_model_defaults():
    """ClauseFixResult must instantiate with correct default field values."""
    from services.recompilation.clause_fixers import ClauseFixResult

    result = ClauseFixResult(clause="5", description="Test fixer")
    assert result.clause == "5"
    assert result.description == "Test fixer"
    assert result.applied is False
    assert result.before_state == ""
    assert result.after_state == ""
    assert result.error is None


def test_clause_fix_result_model_set_applied():
    """ClauseFixResult must accept applied=True and non-empty state strings."""
    from services.recompilation.clause_fixers import ClauseFixResult

    result = ClauseFixResult(
        clause="6.2.1",
        description="MarkInfo Marked=true",
        applied=True,
        before_state="no MarkInfo",
        after_state="MarkInfo <</Marked true>>",
    )
    assert result.applied is True
    assert result.before_state == "no MarkInfo"
    assert result.after_state == "MarkInfo <</Marked true>>"
    assert result.error is None


def test_clause_fix_result_model_with_error():
    """ClauseFixResult must accept an error string and keep applied=False."""
    from services.recompilation.clause_fixers import ClauseFixResult

    result = ClauseFixResult(
        clause="7.1.10",
        description="ViewerPreferences",
        error="PyMuPDF not available",
    )
    assert result.error == "PyMuPDF not available"
    assert result.applied is False


def test_clause_fix_result_model_serializes():
    """ClauseFixResult.model_dump must produce exactly the expected set of keys."""
    from services.recompilation.clause_fixers import ClauseFixResult

    result = ClauseFixResult(clause="7.18.3", description="Tab order")
    data = result.model_dump()
    assert set(data.keys()) == {
        "clause", "description", "applied", "before_state", "after_state", "error"
    }


# ---------------------------------------------------------------------------
# fix_pdfuaid — clause 5
# ---------------------------------------------------------------------------


@skip_no_fitz
def test_fix_pdfuaid_injects_metadata():
    """fix_pdfuaid must inject pdfuaid:part into a PDF that has no XMP pdfuaid."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_pdfuaid

    pdf_bytes = _make_minimal_pdf()
    out_bytes, result = fix_pdfuaid(pdf_bytes)

    assert result.applied is True
    assert result.error is None
    assert "pdfuaid" in result.after_state

    # Verify XMP in the output PDF contains pdfuaid:part
    doc = fitz.open(stream=out_bytes, filetype="pdf")
    xmp = doc.get_xml_metadata()
    doc.close()
    assert "pdfuaid:part" in xmp


@skip_no_fitz
def test_fix_pdfuaid_idempotent():
    """fix_pdfuaid on a PDF that already has pdfuaid must return applied=False."""
    from services.recompilation.clause_fixers import fix_pdfuaid

    pdf_with_id = _make_pdf_with_pdfuaid()
    out_bytes, result = fix_pdfuaid(pdf_with_id)

    assert result.applied is False
    assert result.error is None
    # Before state must mention the existing pdfuaid
    assert "pdfuaid" in result.before_state


@skip_no_fitz
def test_fix_pdfuaid_second_run_idempotent():
    """Running fix_pdfuaid twice must be safe — second run must report applied=False."""
    from services.recompilation.clause_fixers import fix_pdfuaid

    pdf_bytes = _make_minimal_pdf()
    out_bytes_1, result_1 = fix_pdfuaid(pdf_bytes)
    assert result_1.applied is True

    out_bytes_2, result_2 = fix_pdfuaid(out_bytes_1)
    assert result_2.applied is False
    assert result_2.error is None


@skip_no_fitz
def test_fix_pdfuaid_output_is_valid_pdf():
    """fix_pdfuaid must return bytes that parse as a valid PDF."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_pdfuaid

    pdf_bytes = _make_minimal_pdf()
    out_bytes, _ = fix_pdfuaid(pdf_bytes)

    doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(doc) >= 1
    doc.close()


# ---------------------------------------------------------------------------
# fix_markinfo — clause 6.2.1
# ---------------------------------------------------------------------------


@skip_no_fitz
def test_fix_markinfo_sets_marked():
    """fix_markinfo must set MarkInfo Marked=true on a PDF without MarkInfo."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_markinfo

    pdf_bytes = _make_minimal_pdf()
    out_bytes, result = fix_markinfo(pdf_bytes)

    assert result.applied is True
    assert result.error is None
    assert "true" in result.after_state.lower()

    # Verify the catalog now has MarkInfo Marked=true
    doc = fitz.open(stream=out_bytes, filetype="pdf")
    cat_xref = doc.pdf_catalog()
    markinfo = doc.xref_get_key(cat_xref, "MarkInfo")
    doc.close()
    assert markinfo[0] != "null"
    assert "true" in markinfo[1].lower()


@skip_no_fitz
def test_fix_markinfo_idempotent():
    """fix_markinfo on a PDF that already has Marked=true must return applied=False."""
    from services.recompilation.clause_fixers import fix_markinfo

    pdf_bytes = _make_pdf_with_markinfo()
    _, result = fix_markinfo(pdf_bytes)
    assert result.applied is False
    assert result.error is None


@skip_no_fitz
def test_fix_markinfo_output_is_valid_pdf():
    """fix_markinfo must return bytes that parse as a valid PDF."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_markinfo

    pdf_bytes = _make_minimal_pdf()
    out_bytes, _ = fix_markinfo(pdf_bytes)

    doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(doc) >= 1
    doc.close()


# ---------------------------------------------------------------------------
# fix_displaydoctitle — clause 7.1.10
# ---------------------------------------------------------------------------


@skip_no_fitz
def test_fix_displaydoctitle_sets_true():
    """fix_displaydoctitle must set ViewerPreferences DisplayDocTitle=true."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_displaydoctitle

    pdf_bytes = _make_minimal_pdf()
    out_bytes, result = fix_displaydoctitle(pdf_bytes)

    assert result.applied is True
    assert result.error is None
    assert "DisplayDocTitle" in result.after_state

    # Verify ViewerPreferences in output PDF
    doc = fitz.open(stream=out_bytes, filetype="pdf")
    cat_xref = doc.pdf_catalog()
    vp = doc.xref_get_key(cat_xref, "ViewerPreferences")
    doc.close()
    assert vp[0] != "null"
    assert "DisplayDocTitle" in vp[1]
    assert "true" in vp[1].lower()


@skip_no_fitz
def test_fix_displaydoctitle_idempotent():
    """fix_displaydoctitle on a PDF that already has DisplayDocTitle=true must return applied=False."""
    from services.recompilation.clause_fixers import fix_displaydoctitle

    pdf_bytes = _make_pdf_with_displaydoctitle()
    _, result = fix_displaydoctitle(pdf_bytes)
    assert result.applied is False
    assert result.error is None


@skip_no_fitz
def test_fix_displaydoctitle_output_is_valid_pdf():
    """fix_displaydoctitle must return bytes that parse as a valid PDF."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_displaydoctitle

    pdf_bytes = _make_minimal_pdf()
    out_bytes, _ = fix_displaydoctitle(pdf_bytes)

    doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(doc) >= 1
    doc.close()


# ---------------------------------------------------------------------------
# fix_tabs_s — clause 7.18.3
# ---------------------------------------------------------------------------


@skip_no_fitz
def test_fix_tabs_s_all_pages():
    """fix_tabs_s must set /Tabs /S on all pages of a 2-page PDF."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_tabs_s

    pdf_bytes = _make_minimal_pdf(num_pages=2)
    out_bytes, result = fix_tabs_s(pdf_bytes)

    assert result.applied is True
    assert result.error is None
    # after_state should mention both pages were fixed
    assert "2" in result.after_state

    # Verify all pages have /Tabs /S in output
    doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(doc) == 2
    for page_num in range(len(doc)):
        page = doc[page_num]
        tabs = doc.xref_get_key(page.xref, "Tabs")
        assert tabs[1] == "/S", f"Page {page_num} missing /Tabs /S"
    doc.close()


@skip_no_fitz
def test_fix_tabs_s_single_page():
    """fix_tabs_s on a 1-page PDF must set /Tabs /S on the single page."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_tabs_s

    pdf_bytes = _make_minimal_pdf(num_pages=1)
    out_bytes, result = fix_tabs_s(pdf_bytes)

    assert result.applied is True
    assert result.error is None

    doc = fitz.open(stream=out_bytes, filetype="pdf")
    page = doc[0]
    tabs = doc.xref_get_key(page.xref, "Tabs")
    doc.close()
    assert tabs[1] == "/S"


@skip_no_fitz
def test_fix_tabs_s_idempotent():
    """fix_tabs_s on a PDF where all pages already have /Tabs /S must return applied=False."""
    from services.recompilation.clause_fixers import fix_tabs_s

    pdf_bytes = _make_pdf_with_tabs_s_all_pages(num_pages=2)
    _, result = fix_tabs_s(pdf_bytes)
    assert result.applied is False
    assert result.error is None


@skip_no_fitz
def test_fix_tabs_s_five_pages():
    """fix_tabs_s on a 5-page PDF must set /Tabs /S on all five pages."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_tabs_s

    pdf_bytes = _make_minimal_pdf(num_pages=5)
    out_bytes, result = fix_tabs_s(pdf_bytes)

    assert result.applied is True

    doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(doc) == 5
    for page_num in range(5):
        page = doc[page_num]
        tabs = doc.xref_get_key(page.xref, "Tabs")
        assert tabs[1] == "/S", f"Page {page_num} missing /Tabs /S"
    doc.close()


@skip_no_fitz
def test_fix_tabs_s_output_is_valid_pdf():
    """fix_tabs_s must return bytes that parse as a valid PDF."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_tabs_s

    pdf_bytes = _make_minimal_pdf(num_pages=2)
    out_bytes, _ = fix_tabs_s(pdf_bytes)

    doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(doc) == 2
    doc.close()


# ---------------------------------------------------------------------------
# fix_cidset — clause 7.21.4.2
# ---------------------------------------------------------------------------


@skip_no_fitz
def test_fix_cidset_graceful_noop_when_no_cidset():
    """fix_cidset on a PDF with no CIDSet entries must return applied=False without error."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_cidset

    pdf_bytes = _make_minimal_pdf()
    out_bytes, result = fix_cidset(pdf_bytes)

    # A minimal fitz PDF has no CIDFont entries, so no CIDSet to remove
    assert result.applied is False
    assert result.error is None
    # On no-op path the source module returns the original bytes object unchanged
    assert out_bytes == pdf_bytes


@skip_no_fitz
def test_fix_cidset_returns_clause_result():
    """fix_cidset must always return a ClauseFixResult with clause='7.21.4.2'."""
    from services.recompilation.clause_fixers import fix_cidset

    pdf_bytes = _make_minimal_pdf()
    _, result = fix_cidset(pdf_bytes)

    assert result.clause == "7.21.4.2"
    assert "CIDSet" in result.description or "cidset" in result.description.lower()


@skip_no_fitz
def test_fix_cidset_output_is_valid_pdf_when_noop():
    """fix_cidset no-op must still return bytes parseable as a valid PDF."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import fix_cidset

    pdf_bytes = _make_minimal_pdf()
    out_bytes, _ = fix_cidset(pdf_bytes)

    doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(doc) >= 1
    doc.close()


# ---------------------------------------------------------------------------
# ClauseFixerPipeline — apply_all
# ---------------------------------------------------------------------------


@skip_no_fitz
def test_pipeline_runs_all_fixers():
    """ClauseFixerPipeline.apply_all must return exactly 5 ClauseFixResult objects."""
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    pdf_bytes = _make_minimal_pdf()
    pipeline = ClauseFixerPipeline()
    out_bytes, results = pipeline.apply_all(pdf_bytes)

    assert len(results) == 5
    clauses = {r.clause for r in results}
    assert "5" in clauses
    assert "6.2.1" in clauses
    assert "7.1.10" in clauses
    assert "7.18.3" in clauses
    assert "7.21.4.2" in clauses


@skip_no_fitz
def test_pipeline_returns_valid_pdf():
    """ClauseFixerPipeline.apply_all must return bytes that parse as a valid PDF."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    pdf_bytes = _make_minimal_pdf()
    pipeline = ClauseFixerPipeline()
    out_bytes, _ = pipeline.apply_all(pdf_bytes)

    doc = fitz.open(stream=out_bytes, filetype="pdf")
    assert len(doc) >= 1
    doc.close()


@skip_no_fitz
def test_pipeline_results_have_no_unexpected_errors():
    """All 5 pipeline fixers must complete without errors on a minimal PDF."""
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    pdf_bytes = _make_minimal_pdf()
    pipeline = ClauseFixerPipeline()
    _, results = pipeline.apply_all(pdf_bytes)

    errors = [r for r in results if r.error is not None]
    assert errors == [], f"Unexpected fixer errors: {[(r.clause, r.error) for r in errors]}"


@skip_no_fitz
def test_pipeline_fixers_applied_on_fresh_pdf():
    """On a fresh minimal PDF, pdfuaid / markinfo / displaydoctitle / tabs_s must all apply."""
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    pdf_bytes = _make_minimal_pdf()
    pipeline = ClauseFixerPipeline()
    _, results = pipeline.apply_all(pdf_bytes)

    result_map = {r.clause: r for r in results}
    # These four fixers must fire on a fresh PDF (no pre-existing values)
    assert result_map["5"].applied is True, "pdfuaid fixer should have applied"
    assert result_map["6.2.1"].applied is True, "markinfo fixer should have applied"
    assert result_map["7.1.10"].applied is True, "displaydoctitle fixer should have applied"
    assert result_map["7.18.3"].applied is True, "tabs_s fixer should have applied"


@skip_no_fitz
def test_pipeline_idempotent_on_already_fixed_pdf():
    """Running the pipeline twice must have all fixers returning applied=False on second run."""
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    pdf_bytes = _make_minimal_pdf()
    pipeline = ClauseFixerPipeline()
    out_bytes_1, _ = pipeline.apply_all(pdf_bytes)

    # Second run — everything already set
    _, results_2 = pipeline.apply_all(out_bytes_1)
    for result in results_2:
        if result.error is None:
            assert result.applied is False, (
                f"Fixer {result.clause} should be idempotent "
                f"but returned applied=True on second run"
            )


# ---------------------------------------------------------------------------
# ClauseFixerPipeline — with RemediationEventCollector
# ---------------------------------------------------------------------------


@skip_no_fitz
def test_pipeline_with_collector_records_events():
    """ClauseFixerPipeline with a collector must record events for every applied fixer."""
    from services.common.remediation_events import RemediationEventCollector
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    collector = RemediationEventCollector(document_id="doc-test-001", task_id="task-001")

    pdf_bytes = _make_minimal_pdf()
    pipeline = ClauseFixerPipeline(collector=collector)
    _, results = pipeline.apply_all(pdf_bytes)

    events = collector.events()
    applied_results = [r for r in results if r.applied]

    # Every applied fix should have recorded at least one event
    assert len(events) >= len(applied_results), (
        f"Expected >= {len(applied_results)} events for {len(applied_results)} applied fixers, "
        f"got {len(events)}"
    )


@skip_no_fitz
def test_pipeline_with_collector_event_sources_are_clause_fixer():
    """All events recorded by the clause fixer pipeline must have source='clause_fixer'."""
    from services.common.remediation_events import RemediationEventCollector
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    collector = RemediationEventCollector(document_id="doc-test-002", task_id="task-002")

    pdf_bytes = _make_minimal_pdf()
    pipeline = ClauseFixerPipeline(collector=collector)
    pipeline.apply_all(pdf_bytes)

    for event in collector.events():
        assert event.source == "clause_fixer", (
            f"Expected source='clause_fixer', got '{event.source}' for {event.component}"
        )


@skip_no_fitz
def test_pipeline_without_collector_does_not_crash():
    """ClauseFixerPipeline without a collector must work normally (no AttributeError)."""
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    pdf_bytes = _make_minimal_pdf()
    pipeline = ClauseFixerPipeline(collector=None)
    out_bytes, results = pipeline.apply_all(pdf_bytes)

    assert isinstance(out_bytes, bytes)
    assert len(results) == 5


# ---------------------------------------------------------------------------
# ClauseFixerPipeline — graceful on invalid/garbage input
# ---------------------------------------------------------------------------


@skip_no_fitz
def test_pipeline_graceful_on_invalid_pdf():
    """ClauseFixerPipeline.apply_all must not raise on garbage input."""
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    garbage = b"This is not a PDF at all! \x00\x01\x02\x03" * 100
    pipeline = ClauseFixerPipeline()

    try:
        out_bytes, results = pipeline.apply_all(garbage)
        assert isinstance(out_bytes, bytes)
    except Exception as exc:
        pytest.fail(f"Pipeline raised unexpectedly on garbage input: {exc}")


@skip_no_fitz
def test_pipeline_graceful_on_empty_bytes():
    """ClauseFixerPipeline.apply_all on empty bytes must not crash."""
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    pipeline = ClauseFixerPipeline()

    try:
        out_bytes, results = pipeline.apply_all(b"")
        assert isinstance(out_bytes, bytes)
    except Exception as exc:
        pytest.fail(f"Pipeline raised unexpectedly on empty bytes: {exc}")


# ---------------------------------------------------------------------------
# CRITICAL: StructTreeRoot must NOT be added — primary regression guard
# ---------------------------------------------------------------------------


@skip_no_fitz
def test_no_structtreeroot_added_to_minimal_pdf():
    """CRITICAL: ClauseFixerPipeline must NOT add StructTreeRoot to a minimal PDF.

    Adding StructTreeRoot to an untagged PDF causes 425+ VeraPDF regressions.
    """
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    pdf_bytes = _make_minimal_pdf()
    pipeline = ClauseFixerPipeline()
    out_bytes, _ = pipeline.apply_all(pdf_bytes)

    doc = fitz.open(stream=out_bytes, filetype="pdf")
    cat_xref = doc.pdf_catalog()
    struct_tree = doc.xref_get_key(cat_xref, "StructTreeRoot")
    doc.close()

    assert struct_tree[0] == "null", (
        "REGRESSION: ClauseFixerPipeline added StructTreeRoot to a minimal PDF. "
        "This causes 425+ VeraPDF failures on untagged PDFs. "
        f"Got StructTreeRoot={struct_tree[1]!r}"
    )


@skip_no_fitz
def test_no_structtreeroot_added_to_multipage_pdf():
    """CRITICAL: Pipeline must NOT add StructTreeRoot even on a 3-page PDF."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    pdf_bytes = _make_minimal_pdf(num_pages=3)
    pipeline = ClauseFixerPipeline()
    out_bytes, _ = pipeline.apply_all(pdf_bytes)

    doc = fitz.open(stream=out_bytes, filetype="pdf")
    cat_xref = doc.pdf_catalog()
    struct_tree = doc.xref_get_key(cat_xref, "StructTreeRoot")
    doc.close()

    assert struct_tree[0] == "null", (
        "REGRESSION: ClauseFixerPipeline added StructTreeRoot to a 3-page PDF."
    )


@skip_no_fitz
def test_individual_fixers_do_not_add_structtreeroot():
    """Each individual fixer must not add StructTreeRoot to a minimal PDF."""
    import fitz  # noqa: PLC0415
    from services.recompilation.clause_fixers import (
        fix_cidset,
        fix_displaydoctitle,
        fix_markinfo,
        fix_pdfuaid,
        fix_tabs_s,
    )

    fixers = [fix_pdfuaid, fix_markinfo, fix_displaydoctitle, fix_tabs_s, fix_cidset]

    for fixer in fixers:
        pdf_bytes = _make_minimal_pdf()
        out_bytes, _ = fixer(pdf_bytes)

        doc = fitz.open(stream=out_bytes, filetype="pdf")
        cat_xref = doc.pdf_catalog()
        struct_tree = doc.xref_get_key(cat_xref, "StructTreeRoot")
        doc.close()

        assert struct_tree[0] == "null", (
            f"REGRESSION: {fixer.__name__} added StructTreeRoot to a minimal PDF."
        )


# ---------------------------------------------------------------------------
# fitz unavailable graceful degradation — no fitz needed (flag patched)
# ---------------------------------------------------------------------------


def test_fitz_unavailable_pipeline_returns_original_bytes():
    """When _FITZ_AVAILABLE=False, ClauseFixerPipeline.apply_all must return original bytes."""
    import services.recompilation.clause_fixers as cf_module

    original_flag = cf_module._FITZ_AVAILABLE
    try:
        cf_module._FITZ_AVAILABLE = False
        pipeline = cf_module.ClauseFixerPipeline()
        fake_bytes = b"%PDF-1.4 stub content for fitz unavailable test"

        out_bytes, results = pipeline.apply_all(fake_bytes)
        assert out_bytes == fake_bytes, "Pipeline must return original bytes when fitz unavailable"
        assert len(results) == 1, "Pipeline must return exactly 1 error result"
        assert results[0].error is not None, "Single result must carry an error message"
        assert results[0].clause == "all"
    finally:
        cf_module._FITZ_AVAILABLE = original_flag


def test_fitz_unavailable_fix_pdfuaid_returns_error():
    """When _FITZ_AVAILABLE=False, fix_pdfuaid must return (original_bytes, error_result)."""
    import services.recompilation.clause_fixers as cf_module

    original_flag = cf_module._FITZ_AVAILABLE
    try:
        cf_module._FITZ_AVAILABLE = False
        fake_bytes = b"%PDF-1.4 pdfuaid stub"
        out_bytes, result = cf_module.fix_pdfuaid(fake_bytes)
        assert out_bytes == fake_bytes
        assert result.applied is False
        assert result.error is not None
        assert "PyMuPDF" in result.error
    finally:
        cf_module._FITZ_AVAILABLE = original_flag


def test_fitz_unavailable_fix_markinfo_returns_error():
    """When _FITZ_AVAILABLE=False, fix_markinfo must return (original_bytes, error_result)."""
    import services.recompilation.clause_fixers as cf_module

    original_flag = cf_module._FITZ_AVAILABLE
    try:
        cf_module._FITZ_AVAILABLE = False
        fake_bytes = b"%PDF-1.4 markinfo stub"
        out_bytes, result = cf_module.fix_markinfo(fake_bytes)
        assert out_bytes == fake_bytes
        assert result.applied is False
        assert result.error is not None
    finally:
        cf_module._FITZ_AVAILABLE = original_flag


def test_fitz_unavailable_fix_displaydoctitle_returns_error():
    """When _FITZ_AVAILABLE=False, fix_displaydoctitle must return (original_bytes, error_result)."""
    import services.recompilation.clause_fixers as cf_module

    original_flag = cf_module._FITZ_AVAILABLE
    try:
        cf_module._FITZ_AVAILABLE = False
        fake_bytes = b"%PDF-1.4 displaydoctitle stub"
        out_bytes, result = cf_module.fix_displaydoctitle(fake_bytes)
        assert out_bytes == fake_bytes
        assert result.applied is False
        assert result.error is not None
    finally:
        cf_module._FITZ_AVAILABLE = original_flag


def test_fitz_unavailable_fix_tabs_s_returns_error():
    """When _FITZ_AVAILABLE=False, fix_tabs_s must return (original_bytes, error_result)."""
    import services.recompilation.clause_fixers as cf_module

    original_flag = cf_module._FITZ_AVAILABLE
    try:
        cf_module._FITZ_AVAILABLE = False
        fake_bytes = b"%PDF-1.4 tabs_s stub"
        out_bytes, result = cf_module.fix_tabs_s(fake_bytes)
        assert out_bytes == fake_bytes
        assert result.applied is False
        assert result.error is not None
    finally:
        cf_module._FITZ_AVAILABLE = original_flag


def test_fitz_unavailable_fix_cidset_returns_error():
    """When _FITZ_AVAILABLE=False, fix_cidset must return (original_bytes, error_result)."""
    import services.recompilation.clause_fixers as cf_module

    original_flag = cf_module._FITZ_AVAILABLE
    try:
        cf_module._FITZ_AVAILABLE = False
        fake_bytes = b"%PDF-1.4 cidset stub"
        out_bytes, result = cf_module.fix_cidset(fake_bytes)
        assert out_bytes == fake_bytes
        assert result.applied is False
        assert result.error is not None
    finally:
        cf_module._FITZ_AVAILABLE = original_flag


# ---------------------------------------------------------------------------
# ClauseFixerPipeline — VeraPDF accept/reject integration (mocked)
# ---------------------------------------------------------------------------


@skip_no_fitz
def test_pipeline_accepts_fix_when_verapdf_errors_do_not_increase():
    """Pipeline must accept a fixer when VeraPDF error count does not increase."""
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    mock_verapdf = MagicMock()
    mock_result = MagicMock()
    mock_result.error_count = 5  # constant — no increase
    mock_verapdf.validate_pdfua1.return_value = mock_result

    pdf_bytes = _make_minimal_pdf()
    pipeline = ClauseFixerPipeline(verapdf_client=mock_verapdf)
    out_bytes, results = pipeline.apply_all(pdf_bytes)

    assert isinstance(out_bytes, bytes)
    assert len(results) == 5


@skip_no_fitz
def test_pipeline_rejects_fix_when_verapdf_errors_increase():
    """Pipeline must reject a fixer (applied=False + error) when VeraPDF errors increase."""
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    mock_verapdf = MagicMock()
    call_count = [0]

    def side_effect(pdf_b):
        call_count[0] += 1
        result = MagicMock()
        # First call: baseline = 3 errors. All subsequent: 99 errors → reject every fixer.
        result.error_count = 3 if call_count[0] == 1 else 99
        return result

    mock_verapdf.validate_pdfua1.side_effect = side_effect

    pdf_bytes = _make_minimal_pdf()
    pipeline = ClauseFixerPipeline(verapdf_client=mock_verapdf)
    _, results = pipeline.apply_all(pdf_bytes)

    rejected = [r for r in results if r.error and "Rejected" in (r.error or "")]
    assert len(rejected) >= 1, "Expected at least one fixer to be rejected when errors increase"


# ---------------------------------------------------------------------------
# Pipeline FIXER count guard — ensures no fixer was silently removed
# ---------------------------------------------------------------------------


def test_pipeline_fixer_count_is_five():
    """ClauseFixerPipeline.FIXERS must contain exactly 5 entries."""
    from services.recompilation.clause_fixers import ClauseFixerPipeline

    assert len(ClauseFixerPipeline.FIXERS) == 5


def test_pipeline_fixers_include_all_expected_functions():
    """ClauseFixerPipeline.FIXERS must include all 5 expected fixer functions."""
    from services.recompilation.clause_fixers import (
        ClauseFixerPipeline,
        fix_cidset,
        fix_displaydoctitle,
        fix_markinfo,
        fix_pdfuaid,
        fix_tabs_s,
    )

    expected = {fix_pdfuaid, fix_markinfo, fix_displaydoctitle, fix_tabs_s, fix_cidset}
    actual = set(ClauseFixerPipeline.FIXERS)
    assert actual == expected


# ---------------------------------------------------------------------------
# _apply_clause_fixers integration helper (from pdf_tag_enhancer)
# ---------------------------------------------------------------------------


@skip_no_fitz
def test_apply_clause_fixers_returns_bytes():
    """_apply_clause_fixers integration helper must return bytes."""
    from services.recompilation.pdf_tag_enhancer import _apply_clause_fixers

    pdf_bytes = _make_minimal_pdf()
    result = _apply_clause_fixers(pdf_bytes)
    assert isinstance(result, bytes)
    assert len(result) > 0


@skip_no_fitz
def test_apply_clause_fixers_returns_valid_pdf():
    """_apply_clause_fixers must return bytes parseable as a valid PDF."""
    import fitz  # noqa: PLC0415
    from services.recompilation.pdf_tag_enhancer import _apply_clause_fixers

    pdf_bytes = _make_minimal_pdf()
    result = _apply_clause_fixers(pdf_bytes)

    doc = fitz.open(stream=result, filetype="pdf")
    assert len(doc) >= 1
    doc.close()


@skip_no_fitz
def test_apply_clause_fixers_with_collector():
    """_apply_clause_fixers with a collector must not crash and must return bytes."""
    from services.common.remediation_events import RemediationEventCollector
    from services.recompilation.pdf_tag_enhancer import _apply_clause_fixers

    collector = RemediationEventCollector(document_id="doc-integration-001", task_id="t-001")
    pdf_bytes = _make_minimal_pdf()
    result = _apply_clause_fixers(pdf_bytes, collector=collector)

    assert isinstance(result, bytes)
    assert len(result) > 0


@skip_no_fitz
def test_apply_clause_fixers_graceful_on_garbage():
    """_apply_clause_fixers must return bytes on garbage input without raising."""
    from services.recompilation.pdf_tag_enhancer import _apply_clause_fixers

    garbage = b"Not a PDF \x00\x01\x02\x03" * 50
    result = _apply_clause_fixers(garbage)
    assert isinstance(result, bytes)
