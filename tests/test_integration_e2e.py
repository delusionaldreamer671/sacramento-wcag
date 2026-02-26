"""Integration E2E tests for the Sacramento County WCAG Remediation Pipeline.

Tests cover cross-cutting concerns that span multiple modules:
  - Table deduplication utility (services.common.ir)
  - ValidationMode enum contract (services.common.ir)
  - Image extraction cap (services.ingestion.converter)
  - Playwright PDF fallback (services.ingestion.converter)
  - SVG placeholder for images without src (services.recompilation.pdfua_builder)
  - G3 gate draft mode (services.common.gates)
  - validate_accessibility draft mode (services.recompilation.pdfua_builder)

All tests are self-contained and require no external services or API credentials.
"""

from __future__ import annotations

import sys

import pytest

# ---------------------------------------------------------------------------
# 1. Table deduplication
# ---------------------------------------------------------------------------

from services.common.ir import BlockType, IRBlock, IRPage, dedupe_tables_in_page


def test_dedupe_tables_removes_exact_duplicates():
    """Two identical tables on same page -> only one kept."""
    table_attrs = {"headers": ["A", "B"], "rows": [["1", "2"]]}
    blocks = [
        IRBlock(block_type=BlockType.TABLE, content="Test Table", page_num=1, attributes=table_attrs),
        IRBlock(block_type=BlockType.TABLE, content="Test Table", page_num=1, attributes=table_attrs),
        IRBlock(block_type=BlockType.PARAGRAPH, content="Some text", page_num=1),
    ]
    result = dedupe_tables_in_page(blocks)
    tables = [b for b in result if b.block_type == BlockType.TABLE]
    assert len(tables) == 1
    assert len(result) == 2  # 1 table + 1 paragraph


def test_dedupe_tables_keeps_different_tables():
    """Two different tables on same page -> both kept."""
    blocks = [
        IRBlock(
            block_type=BlockType.TABLE,
            content="Table A",
            page_num=1,
            attributes={"headers": ["X"], "rows": [["1"]]},
        ),
        IRBlock(
            block_type=BlockType.TABLE,
            content="Table B",
            page_num=1,
            attributes={"headers": ["Y"], "rows": [["2"]]},
        ),
    ]
    result = dedupe_tables_in_page(blocks)
    assert len(result) == 2


def test_dedupe_tables_different_pages_not_deduped():
    """Same table content on different pages -> both kept (keyed by page_num)."""
    attrs = {"headers": ["A"], "rows": [["1"]]}
    blocks = [
        IRBlock(block_type=BlockType.TABLE, content="Same", page_num=1, attributes=attrs),
        IRBlock(block_type=BlockType.TABLE, content="Same", page_num=2, attributes=attrs),
    ]
    result = dedupe_tables_in_page(blocks)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# 2. ValidationMode enum
# ---------------------------------------------------------------------------

from services.common.ir import ValidationMode


def test_validation_mode_values():
    """ValidationMode enum values must match expected strings."""
    assert ValidationMode.DRAFT.value == "draft"
    assert ValidationMode.PUBLISH.value == "publish"


def test_validation_mode_from_string():
    """ValidationMode must be constructable from its string values."""
    assert ValidationMode("draft") == ValidationMode.DRAFT
    assert ValidationMode("publish") == ValidationMode.PUBLISH


# ---------------------------------------------------------------------------
# 3. Image cap (300 limit)
# ---------------------------------------------------------------------------


def test_image_cap_is_300():
    """Verify the image extraction function hard-caps at 300 images."""
    import inspect

    from services.ingestion.converter import _extract_images_from_pdf

    source = inspect.getsource(_extract_images_from_pdf)
    assert ">= 300" in source or ">=300" in source, (
        "Expected a hard cap of 300 in _extract_images_from_pdf source; "
        "update the cap value in the assertion if it changed."
    )


# ---------------------------------------------------------------------------
# 4. Playwright PDF function removed — Chromium produces untagged PDFs
# ---------------------------------------------------------------------------


def test_try_playwright_pdf_does_not_exist():
    """_try_playwright_pdf must NOT exist in converter.
    Playwright/Chromium print-to-PDF produces untagged PDFs with no /StructTreeRoot,
    no heading tags, no alt text — shipping that as 'remediated' would be worse
    than the original. The function has been removed; PDF output requires Auto-Tag.
    """
    import services.ingestion.converter as conv_mod
    assert not hasattr(conv_mod, "_try_playwright_pdf"), (
        "_try_playwright_pdf must be removed — Playwright produces untagged PDFs"
    )


def test_pdf_output_without_auto_tag_raises_clear_error():
    """When Auto-Tag is unavailable, PDF output raises a clear RuntimeError explaining why."""
    from unittest.mock import MagicMock, patch
    from services.ingestion.converter import _generate_tagged_pdf
    from services.recompilation.pdfua_builder import PDFUABuilder

    builder = PDFUABuilder(document_id="test", document_title="Test")
    builder.add_element("paragraph", "Content")
    html = builder.build_semantic_html()
    span = MagicMock()

    with pytest.raises(RuntimeError) as exc_info:
        _generate_tagged_pdf(None, None, html, builder, span)

    assert "Auto-Tag" in str(exc_info.value), (
        "Error message must mention Auto-Tag so operators know what to configure"
    )


# ---------------------------------------------------------------------------
# 5. SVG placeholder in image rendering
# ---------------------------------------------------------------------------


def test_image_render_uses_svg_placeholder_when_no_src():
    """When image has no src, builder should use an SVG data URI placeholder."""
    from services.recompilation.pdfua_builder import PDFUABuilder

    builder = PDFUABuilder(document_id="test-doc", document_title="Test")
    builder.add_element("image", "Caption text", {"alt": "A photo"})
    html = builder.build_semantic_html()

    assert "data:image/svg+xml" in html, "Expected SVG data URI placeholder for image without src"
    assert 'alt="A photo"' in html, "Expected alt text to be rendered in <img> tag"


# ---------------------------------------------------------------------------
# 6. G3 gate draft mode
# ---------------------------------------------------------------------------


def test_g3_gate_draft_mode_downgrades_axe():
    """In DRAFT mode, run_gate_g3 must not produce hard_fail on axe_ checks."""
    from services.common.gates import run_gate_g3

    # Minimal valid HTML that satisfies structural P0 checks
    html = (
        '<!DOCTYPE html>'
        '<html lang="en">'
        '<head><title>T</title></head>'
        '<body>'
        '<main><h1>Hi</h1><p>Text</p></main>'
        '</body>'
        '</html>'
    )
    result = run_gate_g3(html, mode=ValidationMode.DRAFT)

    # All axe_ named checks in DRAFT mode must be soft_fail at most, never hard_fail
    axe_checks = [c for c in result.checks if c.check_name.startswith("axe_")]
    for check in axe_checks:
        assert check.status != "hard_fail", (
            f"axe check '{check.check_name}' must not be hard_fail in DRAFT mode "
            f"(status was: {check.status})"
        )


# ---------------------------------------------------------------------------
# 7. validate_accessibility draft mode
# ---------------------------------------------------------------------------


def test_validate_accessibility_draft_mode_serious_dont_block():
    """In DRAFT mode, serious violations alone should not block output."""
    from services.recompilation.pdfua_builder import PDFUABuilder

    builder = PDFUABuilder(document_id="test", document_title="Test")
    # Add only a paragraph (no heading). In a minimal document this is acceptable
    # and should not trigger CRITICAL blocking violations.
    builder.add_element("paragraph", "Just a paragraph.")

    html = builder.build_semantic_html()

    # The builder always wraps content in a proper HTML5 document with lang and title,
    # so CRITICAL violations should be absent.
    result = builder.validate_accessibility(html, mode=ValidationMode.DRAFT)

    assert isinstance(result, dict), "validate_accessibility must return a dict"
    assert "blocked" in result, "Result dict must contain 'blocked' key"
    assert "violations" in result, "Result dict must contain 'violations' key"
    assert "score" in result, "Result dict must contain 'score' key"

    # In DRAFT mode, only CRITICAL violations block. The builder produces valid lang/title,
    # so no critical violations should be present -> blocked must be False.
    critical_violations = result.get("critical_violations", [])
    if not critical_violations:
        assert result["blocked"] is False, (
            "DRAFT mode: blocked must be False when there are no CRITICAL violations"
        )
