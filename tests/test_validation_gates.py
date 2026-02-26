"""Tests for the validation gates (G1-G4) and validation ledger."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.common.gates import (
    GateCheck,
    GateResult,
    build_validation_ledger,
    is_publishable,
    run_gate_g1,
    run_gate_g2,
    run_gate_g3,
    run_gate_g4,
)
from services.common.ir import (
    BlockSource,
    BlockType,
    IRBlock,
    IRDocument,
    IRPage,
)


def _pikepdf_available() -> bool:
    """Check if pikepdf is importable (used for conditional test skipping)."""
    try:
        import pikepdf  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc(blocks_per_page: list[list[IRBlock]], doc_id: str = "test") -> IRDocument:
    pages = []
    for i, block_list in enumerate(blocks_per_page):
        pages.append(IRPage(page_num=i, width=612, height=792, blocks=block_list))
    return IRDocument(
        document_id=doc_id,
        filename="test.pdf",
        page_count=len(pages),
        pages=pages,
    )


def _text_block(content: str = "Sample text") -> IRBlock:
    return IRBlock(block_type=BlockType.PARAGRAPH, content=content)


def _heading_block(content: str = "Heading", level: int = 1) -> IRBlock:
    return IRBlock(
        block_type=BlockType.HEADING, content=content, attributes={"level": level}
    )


# ---------------------------------------------------------------------------
# Gate G1 — Post-extraction checks
# ---------------------------------------------------------------------------


class TestGateG1:
    def test_g1_passes_with_valid_document(self):
        doc = _make_doc([[_text_block("Hello world, this is a test document.")]])
        result = run_gate_g1(doc)
        assert result.passed is True
        assert result.gate_id == "G1"

    def test_g1_fails_with_zero_pages(self):
        doc = _make_doc([])
        result = run_gate_g1(doc)
        assert result.passed is False
        hard_fails = [c for c in result.checks if c.status == "hard_fail"]
        assert len(hard_fails) > 0

    def test_g1_soft_fails_with_empty_page(self):
        doc = _make_doc([[]])  # One page, no blocks
        result = run_gate_g1(doc)
        # Empty page triggers a soft_fail, not a hard_fail — gate still passes
        soft_fails = [c for c in result.checks if c.status == "soft_fail"]
        assert len(soft_fails) > 0

    def test_g1_passes_multipage_document(self):
        doc = _make_doc([
            [_text_block("Page 1 content with enough text to pass")],
            [_text_block("Page 2 content with enough text to pass")],
        ])
        result = run_gate_g1(doc)
        assert result.passed is True


# ---------------------------------------------------------------------------
# Gate G2 — Post-IR merge checks
# ---------------------------------------------------------------------------


class TestGateG2:
    def test_g2_passes_with_valid_ir(self):
        doc = _make_doc([[_text_block(), _heading_block()]])
        result = run_gate_g2(doc)
        assert result.passed is True
        assert result.gate_id == "G2"

    def test_g2_fails_with_duplicate_block_ids(self):
        b1 = _text_block("First")
        b2 = _text_block("Second")
        b2.block_id = b1.block_id  # Force duplicate
        doc = _make_doc([[b1, b2]])
        result = run_gate_g2(doc)
        assert result.passed is False
        dup_checks = [c for c in result.checks if "unique" in c.check_name.lower() or "duplicate" in c.check_name.lower()]
        assert any(c.status in ("hard_fail", "soft_fail") for c in dup_checks)

    def test_g2_passes_with_ascending_page_order(self):
        doc = _make_doc([
            [_text_block("Page 0")],
            [_text_block("Page 1")],
            [_text_block("Page 2")],
        ])
        result = run_gate_g2(doc)
        assert result.passed is True

    def test_g2_checks_valid_block_types(self):
        doc = _make_doc([[_text_block(), _heading_block()]])
        result = run_gate_g2(doc)
        type_checks = [c for c in result.checks if "block_type" in c.check_name.lower() or "valid" in c.check_name.lower()]
        assert all(c.status == "pass" for c in type_checks)


# ---------------------------------------------------------------------------
# Gate G3 — Post-HTML checks
# ---------------------------------------------------------------------------


class TestGateG3:
    @pytest.fixture
    def valid_html(self) -> str:
        return '''<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Test</title></head>
<body>
<main>
<h1>Title</h1>
<h2>Section</h2>
<p>Content text.</p>
<img src="test.png" alt="A test image">
<table><thead><tr><th scope="col">A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>
</main>
</body>
</html>'''

    @pytest.fixture
    def no_lang_html(self) -> str:
        return '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Test</title></head>
<body><main><p>No lang attribute.</p></main></body>
</html>'''

    @pytest.fixture
    def missing_alt_html(self) -> str:
        return '''<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Test</title></head>
<body><main><img src="test.png"></main></body>
</html>'''

    def test_g3_passes_with_valid_html(self, valid_html: str):
        result = run_gate_g3(valid_html)
        assert result.gate_id == "G3"
        # Should pass structural checks at minimum
        structural_checks = [c for c in result.checks if c.check_name.startswith("structural_")]
        assert all(c.status == "pass" for c in structural_checks)

    def test_g3_detects_missing_lang(self, no_lang_html: str):
        result = run_gate_g3(no_lang_html)
        lang_checks = [c for c in result.checks if "lang" in c.check_name.lower()]
        assert any(c.status in ("hard_fail", "soft_fail") for c in lang_checks)

    def test_g3_detects_missing_alt(self, missing_alt_html: str):
        result = run_gate_g3(missing_alt_html)
        alt_checks = [c for c in result.checks if "alt" in c.check_name.lower()]
        assert any(c.status in ("hard_fail", "soft_fail") for c in alt_checks)

    def test_g3_detects_heading_skip(self):
        html = '''<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Test</title></head>
<body><main><h1>Title</h1><h4>Skipped H2 and H3</h4></main></body>
</html>'''
        result = run_gate_g3(html)
        heading_checks = [c for c in result.checks if "heading" in c.check_name.lower()]
        assert any(c.status in ("hard_fail", "soft_fail") for c in heading_checks)


# ---------------------------------------------------------------------------
# Validation Ledger
# ---------------------------------------------------------------------------


class TestValidationLedger:
    def test_build_ledger_from_results(self):
        g1_result = GateResult(
            gate_id="G1",
            passed=True,
            checks=[GateCheck(gate_id="G1", check_name="has_pages", status="pass", severity="critical")],
        )
        g2_result = GateResult(
            gate_id="G2",
            passed=True,
            checks=[GateCheck(gate_id="G2", check_name="unique_ids", status="pass", severity="moderate")],
        )

        ledger = build_validation_ledger("doc-123", "test.pdf", [g1_result, g2_result])
        assert ledger["document_id"] == "doc-123"
        assert len(ledger["gates"]) == 2
        assert ledger["summary"]["total_checks"] == 2
        assert ledger["summary"]["passed"] == 2
        assert ledger["summary"]["hard_fails"] == 0

    def test_ledger_counts_failures(self):
        result = GateResult(
            gate_id="G3",
            passed=False,
            checks=[
                GateCheck(gate_id="G3", check_name="lang_check", status="hard_fail", severity="critical"),
                GateCheck(gate_id="G3", check_name="alt_check", status="soft_fail", severity="serious"),
                GateCheck(gate_id="G3", check_name="heading_check", status="pass", severity="moderate"),
            ],
        )

        ledger = build_validation_ledger("doc-456", "test.pdf", [result])
        assert ledger["summary"]["hard_fails"] == 1
        assert ledger["summary"]["soft_fails"] == 1
        assert ledger["summary"]["passed"] == 1

    def test_empty_ledger(self):
        ledger = build_validation_ledger("doc-empty", "empty.pdf", [])
        assert ledger["gates"] == []
        assert ledger["summary"]["total_checks"] == 0


# ---------------------------------------------------------------------------
# GateResult / GateCheck models
# ---------------------------------------------------------------------------


class TestGateModels:
    def test_gate_check_creation(self):
        check = GateCheck(
            gate_id="G1",
            check_name="has_pages",
            status="pass",
            severity="critical",
            details="Document has 5 pages",
        )
        assert check.gate_id == "G1"
        assert check.status == "pass"

    def test_gate_result_creation(self):
        result = GateResult(gate_id="G1", passed=True, checks=[])
        assert result.gate_id == "G1"
        assert result.retry_count == 0

    def test_gate_result_with_retries(self):
        result = GateResult(gate_id="G3", passed=False, checks=[], retry_count=2)
        assert result.retry_count == 2


# ---------------------------------------------------------------------------
# MEDIUM-5.20: _RE_IMG_EMPTY_SRC matches both quote styles
# ---------------------------------------------------------------------------


class TestImgEmptySrcQuoteStyles:
    def test_empty_src_double_quotes_detected(self):
        html = '''<!DOCTYPE html>
<html lang="en"><head><title>T</title></head>
<body><main><h1>Title</h1><img src="" alt="empty double"></main></body></html>'''
        result = run_gate_g3(html)
        src_checks = [c for c in result.checks if c.check_name == "img_src"]
        assert len(src_checks) == 1
        assert src_checks[0].status == "hard_fail"

    def test_empty_src_single_quotes_detected(self):
        html = '''<!DOCTYPE html>
<html lang="en"><head><title>T</title></head>
<body><main><h1>Title</h1><img src='' alt="empty single"></main></body></html>'''
        result = run_gate_g3(html)
        src_checks = [c for c in result.checks if c.check_name == "img_src"]
        assert len(src_checks) == 1
        assert src_checks[0].status == "hard_fail"

    def test_valid_src_passes(self):
        html = '''<!DOCTYPE html>
<html lang="en"><head><title>T</title></head>
<body><main><h1>Title</h1><img src="photo.png" alt="valid"></main></body></html>'''
        result = run_gate_g3(html)
        src_checks = [c for c in result.checks if c.check_name == "img_src"]
        assert len(src_checks) == 0  # no img_src failure


# ---------------------------------------------------------------------------
# HIGH-5.9: G3 table header check — per-table granularity
# ---------------------------------------------------------------------------


class TestG3TableHeaderPerTable:
    def test_single_table_with_headers_passes(self):
        html = '''<!DOCTYPE html>
<html lang="en"><head><title>T</title></head>
<body><main><h1>Title</h1>
<table><thead><tr><th scope="col">A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>
</main></body></html>'''
        result = run_gate_g3(html)
        th_checks = [c for c in result.checks if c.check_name == "table_headers"]
        assert len(th_checks) == 1
        assert th_checks[0].status == "pass"
        assert "1 table" in th_checks[0].details

    def test_single_table_without_headers_fails(self):
        html = '''<!DOCTYPE html>
<html lang="en"><head><title>T</title></head>
<body><main><h1>Title</h1>
<table><tr><td>No headers</td></tr></table>
</main></body></html>'''
        result = run_gate_g3(html)
        th_checks = [c for c in result.checks if c.check_name == "table_headers"]
        assert len(th_checks) == 1
        assert th_checks[0].status == "hard_fail"

    def test_multi_table_one_missing_headers_fails(self):
        """Two tables: one with headers, one without. Gate should fail."""
        html = '''<!DOCTYPE html>
<html lang="en"><head><title>T</title></head>
<body><main><h1>Title</h1>
<table><thead><tr><th scope="col">Good</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>
<table><tr><td>No headers here</td></tr></table>
</main></body></html>'''
        result = run_gate_g3(html)
        th_checks = [c for c in result.checks if c.check_name == "table_headers"]
        assert len(th_checks) == 1
        assert th_checks[0].status == "hard_fail"
        assert "1 of 2" in th_checks[0].details

    def test_multi_table_all_headers_passes(self):
        """Two tables both with proper headers. Gate should pass."""
        html = '''<!DOCTYPE html>
<html lang="en"><head><title>T</title></head>
<body><main><h1>Title</h1>
<table><thead><tr><th scope="col">A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>
<table><thead><tr><th scope="row">B</th><td>2</td></tr></thead></table>
</main></body></html>'''
        result = run_gate_g3(html)
        th_checks = [c for c in result.checks if c.check_name == "table_headers"]
        assert len(th_checks) == 1
        assert th_checks[0].status == "pass"
        assert "2 table" in th_checks[0].details

    def test_no_tables_no_check(self):
        """When no tables present, no table_headers check should appear."""
        html = '''<!DOCTYPE html>
<html lang="en"><head><title>T</title></head>
<body><main><h1>Title</h1><p>No tables here.</p></main></body></html>'''
        result = run_gate_g3(html)
        th_checks = [c for c in result.checks if c.check_name == "table_headers"]
        assert len(th_checks) == 0


# ---------------------------------------------------------------------------
# CRITICAL-5.4: G4 fallback — pikepdf-based PDF/UA checks
# ---------------------------------------------------------------------------


class TestG4FallbackPikepdf:
    """Test that G4 fallback with pikepdf checks /StructTreeRoot, /MarkInfo, /Lang."""

    def _make_tagged_pdf(self) -> bytes:
        """Create a minimal tagged PDF with /StructTreeRoot, /MarkInfo, /Lang."""
        import pikepdf
        import io

        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        # Add /StructTreeRoot (minimal)
        struct_tree = pdf.make_indirect(pikepdf.Dictionary({
            "/Type": pikepdf.Name("/StructTreeRoot"),
        }))
        pdf.Root["/StructTreeRoot"] = struct_tree
        # Add /MarkInfo
        pdf.Root["/MarkInfo"] = pdf.make_indirect(pikepdf.Dictionary({
            "/Marked": True,
        }))
        # Add /Lang
        pdf.Root["/Lang"] = pikepdf.String("en")

        buf = io.BytesIO()
        pdf.save(buf)
        return buf.getvalue()

    def _make_untagged_pdf(self) -> bytes:
        """Create a minimal PDF without /StructTreeRoot, /MarkInfo, /Lang."""
        import pikepdf
        import io

        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(612, 792))

        buf = io.BytesIO()
        pdf.save(buf)
        return buf.getvalue()

    @pytest.mark.skipif(
        not _pikepdf_available(), reason="pikepdf not installed"
    )
    def test_g4_fallback_tagged_pdf_passes(self):
        """G4 fallback should pass for a properly tagged PDF."""
        # Mock Adobe as unavailable to force fallback
        with patch("services.common.gates._run_adobe_checker") as mock_adobe:
            mock_adobe.return_value = [GateCheck(
                gate_id="G4", check_name="adobe_checker_unavailable",
                status="soft_fail", severity="serious",
                priority="P1", next_action="flag_hitl",
                details="Adobe not configured",
            )]
            pdf_bytes = self._make_tagged_pdf()
            result = run_gate_g4(pdf_bytes)

        # Should find /StructTreeRoot, /MarkInfo, /Lang checks all passing
        struct_checks = [c for c in result.checks if c.check_name == "pdf_struct_tree_root"]
        assert len(struct_checks) == 1
        assert struct_checks[0].status == "pass"

        mark_checks = [c for c in result.checks if c.check_name == "pdf_mark_info"]
        assert len(mark_checks) == 1
        assert mark_checks[0].status == "pass"

        lang_checks = [c for c in result.checks if c.check_name == "pdf_lang"]
        assert len(lang_checks) == 1
        assert lang_checks[0].status == "pass"

    @pytest.mark.skipif(
        not _pikepdf_available(), reason="pikepdf not installed"
    )
    def test_g4_fallback_untagged_pdf_fails(self):
        """G4 fallback should hard_fail for an untagged PDF."""
        with patch("services.common.gates._run_adobe_checker") as mock_adobe:
            mock_adobe.return_value = [GateCheck(
                gate_id="G4", check_name="adobe_checker_unavailable",
                status="soft_fail", severity="serious",
                priority="P1", next_action="flag_hitl",
                details="Adobe not configured",
            )]
            pdf_bytes = self._make_untagged_pdf()
            result = run_gate_g4(pdf_bytes)

        # Should fail on missing /StructTreeRoot
        struct_checks = [c for c in result.checks if c.check_name == "pdf_struct_tree_root"]
        assert len(struct_checks) == 1
        assert struct_checks[0].status == "hard_fail"
        assert struct_checks[0].priority == "P0"

        # Should fail on missing /Lang
        lang_checks = [c for c in result.checks if c.check_name == "pdf_lang"]
        assert len(lang_checks) == 1
        assert lang_checks[0].status == "hard_fail"

        # Overall gate should fail
        assert result.passed is False

    def test_g4_fallback_no_pikepdf_no_adobe_hard_fails(self):
        """When both Adobe and pikepdf unavailable, gate must hard_fail."""
        with patch("services.common.gates._run_adobe_checker") as mock_adobe:
            mock_adobe.return_value = [GateCheck(
                gate_id="G4", check_name="adobe_checker_unavailable",
                status="soft_fail", severity="serious",
                priority="P1", next_action="flag_hitl",
                details="Adobe not configured",
            )]
            # Mock pikepdf as unavailable
            import sys
            pikepdf_module = sys.modules.get("pikepdf")
            sys.modules["pikepdf"] = None  # type: ignore[assignment]
            try:
                # Need a valid PDF for pypdf to parse
                import io
                from pypdf import PdfWriter
                writer = PdfWriter()
                writer.add_blank_page(width=612, height=792)
                buf = io.BytesIO()
                writer.write(buf)
                pdf_bytes = buf.getvalue()

                result = run_gate_g4(pdf_bytes)
            finally:
                if pikepdf_module is not None:
                    sys.modules["pikepdf"] = pikepdf_module
                else:
                    del sys.modules["pikepdf"]

        # Should have a hard_fail for no validator
        no_validator = [c for c in result.checks if c.check_name == "pdf_no_validator"]
        assert len(no_validator) == 1
        assert no_validator[0].status == "hard_fail"
        assert no_validator[0].priority == "P0"
        assert result.passed is False


# ---------------------------------------------------------------------------
# HIGH-5.13: is_publishable — VeraPDF unavailability bypass when Adobe passed
# ---------------------------------------------------------------------------


class TestIsPublishableVeraPDFBypass:
    def test_verapdf_unavail_blocks_without_adobe(self):
        """VeraPDF P1 unavailability blocks when Adobe did NOT pass."""
        verapdf_result = GateResult(
            gate_id="G4-VeraPDF", passed=False,
            checks=[GateCheck(
                gate_id="G4-VeraPDF", check_name="verapdf_available",
                status="soft_fail", severity="moderate",
                priority="P1", next_action="flag_hitl",
                details="VeraPDF container not reachable — PDF/UA-1 validation SKIPPED (status: unavailable)",
            )],
        )
        # No adobe_checker_pass in G4
        g4_result = GateResult(
            gate_id="G4", passed=True,
            checks=[GateCheck(
                gate_id="G4", check_name="pdf_has_pages",
                status="pass", severity="minor",
                priority="P2", next_action="proceed",
                details="PDF has 5 pages",
            )],
        )
        publishable, reason = is_publishable([g4_result, verapdf_result])
        assert publishable is False
        assert "P1" in reason

    def test_verapdf_unavail_does_not_block_when_adobe_passed(self):
        """VeraPDF P1 unavailability does NOT block when Adobe G4 explicitly passed."""
        verapdf_result = GateResult(
            gate_id="G4-VeraPDF", passed=False,
            checks=[GateCheck(
                gate_id="G4-VeraPDF", check_name="verapdf_available",
                status="soft_fail", severity="moderate",
                priority="P1", next_action="flag_hitl",
                details="VeraPDF container not reachable — PDF/UA-1 validation SKIPPED (status: unavailable)",
            )],
        )
        # Adobe G4 explicitly passed
        g4_result = GateResult(
            gate_id="G4", passed=True,
            checks=[GateCheck(
                gate_id="G4", check_name="adobe_checker_pass",
                status="pass", severity="minor",
                priority="P2", next_action="proceed",
                details="Adobe PDF Accessibility Checker: compliant",
            )],
        )
        publishable, reason = is_publishable([g4_result, verapdf_result])
        assert publishable is True
        assert "publishable" in reason.lower()

    def test_verapdf_real_failure_still_blocks_with_adobe(self):
        """VeraPDF real compliance P1 failures still block even when Adobe passed."""
        verapdf_result = GateResult(
            gate_id="G4-VeraPDF", passed=False,
            checks=[GateCheck(
                gate_id="G4-VeraPDF", check_name="clause_7.1",
                status="soft_fail", severity="serious",
                priority="P1", next_action="flag_hitl",
                details="Clause 7.1: General requirements (3 failures)",
            )],
        )
        g4_result = GateResult(
            gate_id="G4", passed=True,
            checks=[GateCheck(
                gate_id="G4", check_name="adobe_checker_pass",
                status="pass", severity="minor",
                priority="P2", next_action="proceed",
                details="Adobe PDF Accessibility Checker: compliant",
            )],
        )
        publishable, reason = is_publishable([g4_result, verapdf_result])
        assert publishable is False
        assert "P1" in reason


# ---------------------------------------------------------------------------
# HIGH-2.7/2.8: Exception specificity — Python bugs should propagate
# ---------------------------------------------------------------------------


class TestExceptionSpecificity:
    def test_axe_type_error_propagates(self):
        """TypeError in axe runner should NOT be caught (programming bug)."""
        with patch("services.common.gates._run_axe_checks") as mock_axe:
            mock_axe.side_effect = TypeError("unexpected type in axe code")
            with pytest.raises(TypeError, match="unexpected type"):
                run_gate_g3("<html lang='en'><body></body></html>")

    def test_axe_import_error_caught_as_soft_fail(self):
        """ImportError in axe runner should be caught as soft_fail, not crash."""
        from services.common.gates import _run_axe_checks

        # Simulate the axe_runner import failing with ImportError
        with patch.dict("sys.modules", {"services.common.axe_runner": None}):
            checks = _run_axe_checks("<html></html>")
        # Should produce a soft_fail check, not crash
        assert any(c.status == "soft_fail" for c in checks)

    def test_adobe_type_error_propagates(self):
        """TypeError in Adobe checker should NOT be caught (programming bug)."""
        from services.common.gates import _run_adobe_checker

        # Patch config.settings to indicate Adobe is configured
        mock_settings = MagicMock()
        mock_settings.adobe_client_id = "test-id"
        mock_settings.adobe_checker_enabled = True

        with patch.dict("sys.modules", {
            "services.common.config": MagicMock(settings=mock_settings),
        }):
            # Patch the adobe_checker module to raise TypeError on import
            mock_checker_module = MagicMock()
            mock_checker_module.AdobeAccessibilityChecker.side_effect = TypeError("bug in checker")
            with patch.dict("sys.modules", {
                "services.extraction.adobe_checker": mock_checker_module,
            }):
                with pytest.raises(TypeError, match="bug in checker"):
                    _run_adobe_checker(b"%PDF-test")
