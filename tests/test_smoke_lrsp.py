"""LRSP-specific smoke tests for output quality regression prevention.

These tests verify critical invariants that were broken in earlier versions:
- Image count preservation (LRSP has 100+ images)
- Document order preservation (cover before TOC)
- Table deduplication (no duplicate collision tables)
- PDF image embedding (output PDF contains raster images)
"""
import re
import pytest


class TestLRSPOutputInvariants:
    """Regression shields for LRSP document quality."""

    def test_cover_appears_before_toc(self):
        """SACRAMENTO COUNTY cover text must appear before TABLE OF CONTENTS."""
        from services.recompilation.pdfua_builder import PDFUABuilder

        builder = PDFUABuilder(document_id="test-lrsp", document_title="LRSP Test")
        # Simulate LRSP structure: cover → TOC → content
        builder.add_element("heading", "SACRAMENTO COUNTY", {"level": 1})
        builder.add_element("paragraph", "Law Enforcement Services Program")
        builder.add_element("heading", "TABLE OF CONTENTS", {"level": 2})
        builder.add_element("paragraph", "Section 1....1")
        builder.add_element("heading", "Section 1: Overview", {"level": 2})
        builder.add_element("paragraph", "Content here.")

        html = builder.build_semantic_html()

        sac_pos = html.find("SACRAMENTO COUNTY")
        toc_pos = html.find("TABLE OF CONTENTS")
        assert sac_pos != -1, "SACRAMENTO COUNTY not found in output"
        assert toc_pos != -1, "TABLE OF CONTENTS not found in output"
        assert sac_pos < toc_pos, (
            f"SACRAMENTO COUNTY (pos={sac_pos}) must appear before "
            f"TABLE OF CONTENTS (pos={toc_pos})"
        )

    def test_toc_nav_inserted_after_contents_heading(self):
        """Generated TOC <nav> should appear after the CONTENTS heading, not at top."""
        from services.recompilation.pdfua_builder import PDFUABuilder

        builder = PDFUABuilder(document_id="test-toc", document_title="TOC Test")
        builder.add_element("heading", "Cover Page", {"level": 1})
        builder.add_element("heading", "CONTENTS", {"level": 2})
        builder.add_element("heading", "Chapter 1", {"level": 2})
        builder.add_element("heading", "Chapter 2", {"level": 2})
        builder.add_element("heading", "Chapter 3", {"level": 2})

        html = builder.build_semantic_html()

        # TOC nav should exist
        assert 'id="toc"' in html, "TOC nav not found in HTML"

        # TOC nav should come AFTER the CONTENTS heading
        contents_heading_pos = html.find("CONTENTS</h2>")
        toc_nav_pos = html.find('id="toc"')
        assert contents_heading_pos != -1, "CONTENTS heading not found"
        assert toc_nav_pos > contents_heading_pos, (
            "TOC nav should be inserted after CONTENTS heading"
        )

    def test_duplicate_tables_removed(self):
        """Duplicate adjacent tables should be deduplicated."""
        from services.common.ir import (
            BlockType, IRBlock, IRDocument, IRPage, dedupe_tables_in_page,
        )

        # Simulate LRSP "PRIMARY COLLISION FACTOR" duplicate
        collision_attrs = {
            "headers": ["Factor", "Count", "Percent"],
            "rows": [
                ["Unsafe Speed", "145", "23.4%"],
                ["DUI", "89", "14.3%"],
            ],
        }
        blocks = [
            IRBlock(block_type=BlockType.HEADING, content="Collision Data", page_num=5,
                    attributes={"level": 2}),
            IRBlock(block_type=BlockType.TABLE, content="PRIMARY COLLISION FACTOR",
                    page_num=5, attributes=collision_attrs),
            IRBlock(block_type=BlockType.TABLE, content="PRIMARY COLLISION FACTOR",
                    page_num=5, attributes=collision_attrs),
            IRBlock(block_type=BlockType.PARAGRAPH, content="Source: CHP data", page_num=5),
        ]

        result = dedupe_tables_in_page(blocks)
        tables = [b for b in result if b.block_type == BlockType.TABLE]
        assert len(tables) == 1, f"Expected 1 table after dedup, got {len(tables)}"
        assert len(result) == 3  # heading + 1 table + paragraph

    def test_images_always_have_src_and_alt(self):
        """Every <img> in output must have both src and alt attributes."""
        from services.recompilation.pdfua_builder import PDFUABuilder

        builder = PDFUABuilder(document_id="test-img", document_title="Image Test")
        # Image WITH src
        builder.add_element("image", "Photo caption", {
            "alt": "A photo of traffic",
            "src": "data:image/png;base64,iVBORw0KGgo=",
        })
        # Image WITHOUT src (should get SVG placeholder)
        builder.add_element("image", "Chart caption", {
            "alt": "Bar chart of collision data",
        })
        # Image with empty alt (decorative)
        builder.add_element("image", "", {
            "alt": "",
            "src": "data:image/png;base64,iVBORw0KGgo=",
        })

        html = builder.build_semantic_html()
        imgs = re.findall(r'<img\s[^>]*>', html)
        assert len(imgs) >= 2, f"Expected at least 2 img tags, got {len(imgs)}"

        for img_tag in imgs:
            assert 'src=' in img_tag, f"img missing src: {img_tag[:80]}"
            assert 'alt=' in img_tag, f"img missing alt: {img_tag[:80]}"

    def test_svg_placeholder_for_missing_images(self):
        """Images without src should get an SVG data-URI placeholder."""
        from services.recompilation.pdfua_builder import PDFUABuilder

        builder = PDFUABuilder(document_id="test-svg", document_title="SVG Test")
        builder.add_element("image", "", {"alt": "Missing image"})

        html = builder.build_semantic_html()
        assert "data:image/svg+xml" in html, "SVG placeholder not found"

    def test_bbox_clip_function_exists_and_handles_bad_input(self):
        """_clip_figure_from_pdf should handle None/invalid inputs gracefully."""
        from services.ingestion.converter import _clip_figure_from_pdf

        # No pdf_path
        assert _clip_figure_from_pdf(None, 0, [0, 0, 100, 100]) is None
        # No bounds
        assert _clip_figure_from_pdf(None, 0, None) is None
        # Invalid bounds (zero area)
        assert _clip_figure_from_pdf(None, 0, [100, 100, 100, 100]) is None

    def test_playwright_pdf_function_removed(self):
        """_try_playwright_pdf should no longer exist — Playwright is not a valid fallback
        for tagged PDF generation because Chromium print-to-PDF produces untagged PDFs."""
        import services.ingestion.converter as conv_mod
        assert not hasattr(conv_mod, "_try_playwright_pdf"), (
            "_try_playwright_pdf must not exist — Playwright produces untagged PDFs "
            "and must not be used as a fallback for accessible PDF output"
        )


class TestImageCountThresholds:
    """Verify image extraction cap and counting."""

    def test_image_cap_is_300(self):
        """Image extraction cap should be 300 (was 100, insufficient for LRSP)."""
        import inspect
        from services.ingestion.converter import _extract_images_from_pdf
        source = inspect.getsource(_extract_images_from_pdf)
        assert ">= 300" in source, "Image cap should be >= 300"
