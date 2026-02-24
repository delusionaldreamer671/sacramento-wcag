"""Tests for the validation gates (G1-G4) and validation ledger."""

from __future__ import annotations

import pytest

from services.common.gates import (
    GateCheck,
    GateResult,
    build_validation_ledger,
    run_gate_g1,
    run_gate_g2,
    run_gate_g3,
)
from services.common.ir import (
    BlockSource,
    BlockType,
    IRBlock,
    IRDocument,
    IRPage,
)


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
