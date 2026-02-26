"""Tests for services/recompilation/pdfua_builder.py.

Covers PDFUABuilder.add_element, build_semantic_html, generate_pdfua,
validate_accessibility, and generate_manual_review_csv.
No external services are called — reportlab runs in-process.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

import pytest

from services.common.models import HITLReviewItem
from services.recompilation.pdfua_builder import PDFUABuilder, _validate_heading_sequence


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def builder() -> PDFUABuilder:
    """A fresh PDFUABuilder instance for each test."""
    return PDFUABuilder(document_id="test-doc-001", document_title="Test Document")


@pytest.fixture
def populated_builder(builder: PDFUABuilder) -> PDFUABuilder:
    """A builder pre-loaded with one of each common element type."""
    builder.add_element("heading", "Annual Report", {"level": 1})
    builder.add_element("paragraph", "Introduction paragraph content.")
    builder.add_element("image", "", {"alt": "County seal logo", "src": "seal.png"})
    builder.add_element(
        "table",
        "Budget Summary",
        {
            "headers": ["Department", "Budget", "Spent"],
            "rows": [
                ["Public Safety", "$12M", "$11M"],
                ["Health Services", "$8M", "$7.5M"],
            ],
        },
    )
    builder.add_element("list", "", {"items": ["Item A", "Item B", "Item C"]})
    builder.add_element("link", "Full report", {"href": "https://saccounty.gov/report"})
    return builder


def _make_hitl_item(
    item_id: str,
    document_id: str = "doc-001",
    element_type: str = "image",
    finding_id: str = "finding-001",
    ai_suggestion: str = "AI drafted alt text",
    reviewer_decision: str | None = None,
) -> HITLReviewItem:
    return HITLReviewItem(
        id=item_id,
        document_id=document_id,
        finding_id=finding_id,
        element_type=element_type,
        original_content={"Path": "//Document/Figure"},
        ai_suggestion=ai_suggestion,
        reviewer_decision=reviewer_decision,
        reviewed_at=datetime(2026, 2, 23, 12, 0, tzinfo=timezone.utc)
        if reviewer_decision
        else None,
        reviewed_by="reviewer@saccounty.gov" if reviewer_decision else None,
    )


# ---------------------------------------------------------------------------
# add_element — individual element types
# ---------------------------------------------------------------------------


def test_add_heading_element(builder: PDFUABuilder):
    """add_element with type 'heading' must store the element without raising."""
    builder.add_element("heading", "Section Title", {"level": 2})
    html = builder.build_semantic_html()
    assert "<h2" in html
    assert "Section Title</h2>" in html


def test_add_paragraph_element(builder: PDFUABuilder):
    """add_element with type 'paragraph' must produce a <p> tag in the output."""
    builder.add_element("paragraph", "Body copy content.")
    html = builder.build_semantic_html()
    assert "<p>Body copy content.</p>" in html


def test_add_image_element_with_alt_text(builder: PDFUABuilder):
    """add_element with type 'image' and an alt attribute must produce an <img alt=...> tag."""
    builder.add_element("image", "", {"alt": "Sacramento County seal", "src": "seal.png"})
    html = builder.build_semantic_html()
    assert 'alt="Sacramento County seal"' in html


def test_add_table_element_with_headers(builder: PDFUABuilder):
    """add_element with type 'table' and headers must render <th scope="col"> cells."""
    builder.add_element(
        "table",
        "Budget",
        {"headers": ["Year", "Amount"], "rows": [["2025", "$1M"]]},
    )
    html = builder.build_semantic_html()
    assert 'scope="col"' in html
    assert "<th" in html


def test_invalid_element_type_raises_value_error(builder: PDFUABuilder):
    """add_element with an unrecognised type must raise ValueError immediately."""
    with pytest.raises(ValueError):
        builder.add_element("footnote", "Some text")


def test_add_element_type_is_case_insensitive(builder: PDFUABuilder):
    """Element type matching is case-insensitive — 'Heading' must be accepted."""
    builder.add_element("Heading", "Uppercase test", {"level": 3})
    html = builder.build_semantic_html()
    assert "<h3" in html
    assert "Uppercase test</h3>" in html


# ---------------------------------------------------------------------------
# build_semantic_html — structural guarantees
# ---------------------------------------------------------------------------


def test_build_semantic_html_includes_lang_attribute(builder: PDFUABuilder):
    """The output HTML must have a lang attribute on <html> (WCAG 3.1.1)."""
    builder.add_element("paragraph", "Content.")
    html = builder.build_semantic_html()
    assert 'lang="en"' in html


def test_build_semantic_html_includes_heading_hierarchy(builder: PDFUABuilder):
    """Sequential headings must appear in correct H1 → H2 → H3 order."""
    builder.add_element("heading", "Document Title", {"level": 1})
    builder.add_element("heading", "Section One", {"level": 2})
    builder.add_element("heading", "Subsection A", {"level": 3})
    html = builder.build_semantic_html()
    h1_pos = html.index("<h1")
    h2_pos = html.index("<h2")
    h3_pos = html.index("<h3")
    assert h1_pos < h2_pos < h3_pos


def test_build_semantic_html_tables_have_scope(builder: PDFUABuilder):
    """Tables rendered via build_semantic_html must include scope= on <th> cells."""
    builder.add_element(
        "table",
        "Data",
        {"headers": ["Col A", "Col B"], "rows": [["1", "2"]]},
    )
    html = builder.build_semantic_html()
    assert 'scope="col"' in html


def test_build_semantic_html_images_have_alt(builder: PDFUABuilder):
    """Images rendered via build_semantic_html must carry a non-empty alt attribute."""
    builder.add_element("image", "", {"alt": "Chart showing 2025 revenue by quarter"})
    html = builder.build_semantic_html()
    assert 'alt="Chart showing 2025 revenue by quarter"' in html


def test_build_semantic_html_includes_document_title(builder: PDFUABuilder):
    """The <title> element in the HTML head must match the document_title."""
    html = builder.build_semantic_html()
    assert "<title>Test Document</title>" in html


def test_build_semantic_html_contains_html5_doctype(builder: PDFUABuilder):
    """The output must start with a valid HTML5 DOCTYPE declaration."""
    html = builder.build_semantic_html()
    assert html.strip().startswith("<!DOCTYPE html>")


def test_build_semantic_html_empty_builder_returns_valid_shell(builder: PDFUABuilder):
    """An empty builder must still return a complete, valid HTML shell."""
    html = builder.build_semantic_html()
    assert "<html" in html
    assert "</html>" in html
    assert "<body>" in html or "<body" in html


def test_build_semantic_html_list_unordered_renders_ul(builder: PDFUABuilder):
    """An unordered list (ordered=False) must render as <ul> elements."""
    builder.add_element("list", "", {"items": ["Alpha", "Beta"], "ordered": False})
    html = builder.build_semantic_html()
    assert "<ul>" in html
    assert "<li>Alpha</li>" in html


def test_build_semantic_html_list_ordered_renders_ol(builder: PDFUABuilder):
    """An ordered list (ordered=True) must render as <ol> elements."""
    builder.add_element("list", "", {"items": ["Step 1", "Step 2"], "ordered": True})
    html = builder.build_semantic_html()
    assert "<ol>" in html


def test_build_semantic_html_link_renders_anchor_tag(builder: PDFUABuilder):
    """A link element must render as an <a href="..."> tag."""
    builder.add_element("link", "Read more", {"href": "https://saccounty.gov"})
    html = builder.build_semantic_html()
    assert 'href="https://saccounty.gov"' in html
    assert "Read more" in html


# ---------------------------------------------------------------------------
# generate_pdfua
# ---------------------------------------------------------------------------


def test_generate_pdfua_returns_bytes(populated_builder: PDFUABuilder):
    """generate_pdfua must return a bytes object (raw PDF content)."""
    html = populated_builder.build_semantic_html()
    result = populated_builder.generate_pdfua(html)
    assert isinstance(result, bytes)


def test_generate_pdfua_output_starts_with_pdf_magic_bytes(populated_builder: PDFUABuilder):
    """The PDF output must begin with the %PDF magic bytes."""
    html = populated_builder.build_semantic_html()
    pdf_bytes = populated_builder.generate_pdfua(html)
    assert pdf_bytes[:4] == b"%PDF"


def test_generate_pdfua_with_empty_html_returns_bytes(builder: PDFUABuilder):
    """Calling generate_pdfua with empty HTML must not raise and must return bytes."""
    result = builder.generate_pdfua("")
    assert isinstance(result, bytes)
    assert len(result) > 0


def test_generate_pdfua_with_empty_html_returns_pdf_magic_bytes(builder: PDFUABuilder):
    """Even a placeholder PDF returned for empty HTML must start with %PDF."""
    result = builder.generate_pdfua("")
    assert result[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# PDF/UA tagging — /MarkInfo, /Lang, XMP metadata
# ---------------------------------------------------------------------------


def test_generate_pdfua_contains_mark_info(populated_builder: PDFUABuilder):
    """The PDF output must contain /MarkInfo << /Marked true >> (ISO 14289-1 §7.1)."""
    html = populated_builder.build_semantic_html()
    pdf_bytes = populated_builder.generate_pdfua(html)
    assert b"/MarkInfo" in pdf_bytes
    assert b"Marked" in pdf_bytes
    # PDF boolean 'true' (not Python True) must appear
    assert b"true" in pdf_bytes


def test_generate_pdfua_contains_lang_entry(populated_builder: PDFUABuilder):
    """/Lang must appear in the PDF catalog (ISO 14289-1 §7.2)."""
    html = populated_builder.build_semantic_html()
    pdf_bytes = populated_builder.generate_pdfua(html)
    assert b"/Lang" in pdf_bytes


def test_generate_pdfua_lang_matches_document_language():
    """The /Lang value in the PDF must reflect the language set on the builder."""
    b = PDFUABuilder(document_id="lang-test", document_title="Lang Test", language="fr")
    b.add_element("paragraph", "Bonjour.")
    html = b.build_semantic_html()
    pdf_bytes = b.generate_pdfua(html)
    # BCP-47 form: "fr-FR"
    assert b"fr-FR" in pdf_bytes or b"fr" in pdf_bytes


def test_generate_pdfua_contains_xmp_metadata(populated_builder: PDFUABuilder):
    """The PDF output must contain an XMP metadata stream."""
    html = populated_builder.build_semantic_html()
    pdf_bytes = populated_builder.generate_pdfua(html)
    assert b"/Metadata" in pdf_bytes
    assert b"xpacket" in pdf_bytes


def test_generate_pdfua_xmp_contains_pdfua_marker(populated_builder: PDFUABuilder):
    """The XMP metadata must include the pdfuaid:part=1 PDF/UA-1 identifier."""
    html = populated_builder.build_semantic_html()
    pdf_bytes = populated_builder.generate_pdfua(html)
    assert b"pdfuaid" in pdf_bytes
    assert b"<pdfuaid:part>1</pdfuaid:part>" in pdf_bytes


def test_generate_pdfua_xmp_contains_dc_title(populated_builder: PDFUABuilder):
    """The XMP metadata must include a dc:title element."""
    html = populated_builder.build_semantic_html()
    pdf_bytes = populated_builder.generate_pdfua(html)
    assert b"dc:title" in pdf_bytes


def test_generate_pdfua_placeholder_contains_mark_info(builder: PDFUABuilder):
    """Even the minimal placeholder PDF (empty HTML) must set /MarkInfo."""
    pdf_bytes = builder.generate_pdfua("")
    assert b"Marked" in pdf_bytes


def test_generate_pdfua_placeholder_contains_lang(builder: PDFUABuilder):
    """Even the minimal placeholder PDF (empty HTML) must set /Lang."""
    pdf_bytes = builder.generate_pdfua("")
    assert b"/Lang" in pdf_bytes


def test_generate_pdfua_placeholder_contains_xmp(builder: PDFUABuilder):
    """Even the minimal placeholder PDF (empty HTML) must contain XMP metadata."""
    pdf_bytes = builder.generate_pdfua("")
    assert b"pdfuaid" in pdf_bytes


def test_generate_pdfua_output_is_deterministic(populated_builder: PDFUABuilder):
    """Two calls with the same input must produce identical byte output (invariant mode)."""
    html = populated_builder.build_semantic_html()
    pdf_bytes_1 = populated_builder.generate_pdfua(html)
    pdf_bytes_2 = populated_builder.generate_pdfua(html)
    assert pdf_bytes_1 == pdf_bytes_2


# ---------------------------------------------------------------------------
# validate_accessibility
# ---------------------------------------------------------------------------


def test_validate_accessibility_passes_valid_html(builder: PDFUABuilder):
    """A fully compliant HTML document must have no violations and a score of 1.0."""
    builder.add_element("heading", "Report Title", {"level": 1})
    builder.add_element("image", "", {"alt": "Descriptive alt text here", "src": "data:image/png;base64,iVBOR"})
    builder.add_element(
        "table",
        "Summary",
        {"headers": ["Year", "Value"], "rows": [["2025", "100"]]},
    )
    html = builder.build_semantic_html()
    report = builder.validate_accessibility(html)
    assert report["violations"] == []
    assert report["score"] == 1.0


def test_validate_accessibility_detects_missing_alt(builder: PDFUABuilder):
    """An <img> without a non-empty alt attribute must produce a 1.1.1 violation."""
    # Inject raw HTML with a missing alt to bypass builder's alt-text rendering
    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head><title>Test</title></head>\n'
        "<body><main><figure><img></figure></main></body></html>"
    )
    report = builder.validate_accessibility(html)
    criteria = [v["criterion"] for v in report["violations"]]
    assert "1.1.1" in criteria


def test_validate_accessibility_detects_missing_src(builder: PDFUABuilder):
    """An <img> without a src attribute must produce a critical 1.1.1 violation."""
    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head><title>Test</title></head>\n'
        '<body><main><figure><img alt="A chart"></figure>'
        '<figure><img alt="A photo"></figure></main></body></html>'
    )
    report = builder.validate_accessibility(html)
    src_violations = [
        v for v in report["violations"]
        if "missing src" in v.get("description", "")
    ]
    assert len(src_violations) == 1
    assert src_violations[0]["violation_class"] == "critical"
    assert "2" in src_violations[0]["description"]  # "2 <img> element(s)"


def test_validate_accessibility_blocks_on_many_missing_src(builder: PDFUABuilder):
    """When >10% of images lack src, the output must be blocked."""
    # 5 images, none with src — well above 10% threshold
    imgs = '<figure><img alt="img"></figure>' * 5
    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head><title>Test</title></head>\n'
        f'<body><main><h1>Title</h1>{imgs}</main></body></html>'
    )
    report = builder.validate_accessibility(html)
    assert report["blocked"] is True
    assert any("images missing src" in label for label in report["critical_violations"])


def test_validate_accessibility_detects_heading_skip(builder: PDFUABuilder):
    """An H1 → H3 jump (skipping H2) must produce a 2.4.6 violation."""
    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head><title>Test</title></head>\n'
        "<body><main><h1>Title</h1><h3>Subsection</h3></main></body></html>"
    )
    report = builder.validate_accessibility(html)
    criteria = [v["criterion"] for v in report["violations"]]
    assert "2.4.6" in criteria


def test_validate_accessibility_detects_missing_lang(builder: PDFUABuilder):
    """An <html> element without a lang attribute must produce a 3.1.1 violation."""
    html = (
        "<!DOCTYPE html>\n<html>\n<head><title>Test</title></head>\n"
        "<body><main><p>Content</p></main></body></html>"
    )
    report = builder.validate_accessibility(html)
    criteria = [v["criterion"] for v in report["violations"]]
    assert "3.1.1" in criteria


def test_validate_accessibility_detects_table_without_scope(builder: PDFUABuilder):
    """A <table> whose headers lack scope= must produce a 1.3.1 violation."""
    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head><title>Test</title></head>\n'
        "<body><main><table><thead><tr><th>Col</th></tr></thead>"
        "<tbody><tr><td>Val</td></tr></tbody></table></main></body></html>"
    )
    report = builder.validate_accessibility(html)
    criteria = [v["criterion"] for v in report["violations"]]
    assert "1.3.1" in criteria


def test_validate_accessibility_score_is_between_zero_and_one(builder: PDFUABuilder):
    """The accessibility score must always be in the [0.0, 1.0] range."""
    html = builder.build_semantic_html()
    report = builder.validate_accessibility(html)
    assert 0.0 <= report["score"] <= 1.0


def test_validate_accessibility_returns_dict_with_required_keys(builder: PDFUABuilder):
    """validate_accessibility must return a dict with 'violations' and 'score' keys."""
    html = builder.build_semantic_html()
    report = builder.validate_accessibility(html)
    assert "violations" in report
    assert "score" in report


def test_validate_accessibility_violations_is_list(builder: PDFUABuilder):
    """The 'violations' entry in the report must always be a list."""
    html = builder.build_semantic_html()
    report = builder.validate_accessibility(html)
    assert isinstance(report["violations"], list)


def test_validate_accessibility_each_violation_has_required_keys(builder: PDFUABuilder):
    """Each violation dict must have 'criterion', 'severity', and 'description'."""
    html = (
        "<!DOCTYPE html>\n<html>\n<head><title>T</title></head>\n"
        "<body><main><h1>T</h1><h3>S</h3></main></body></html>"
    )
    report = builder.validate_accessibility(html)
    for violation in report["violations"]:
        assert "criterion" in violation
        assert "severity" in violation
        assert "description" in violation


# ---------------------------------------------------------------------------
# generate_manual_review_csv
# ---------------------------------------------------------------------------


def test_generate_manual_review_csv_empty_returns_header_only():
    """An empty items list must produce CSV output with only the header row."""
    csv_output = PDFUABuilder.generate_manual_review_csv([])
    reader = csv.reader(io.StringIO(csv_output))
    rows = list(reader)
    # Only the header row; no data rows
    assert len(rows) == 1
    assert rows[0][0] == "item_id"


def test_generate_manual_review_csv_none_items_returns_header_only():
    """Passing None as items must be treated as an empty list and return header only."""
    csv_output = PDFUABuilder.generate_manual_review_csv(None)
    reader = csv.reader(io.StringIO(csv_output))
    rows = list(reader)
    assert len(rows) == 1


def test_generate_manual_review_csv_header_contains_all_columns():
    """The CSV header row must contain all expected column names."""
    csv_output = PDFUABuilder.generate_manual_review_csv([])
    reader = csv.reader(io.StringIO(csv_output))
    header = next(reader)
    expected_columns = [
        "item_id",
        "document_id",
        "element_type",
        "finding_id",
        "ai_suggestion",
        "reviewer_decision",
        "reviewer_edit",
        "reviewed_by",
        "reviewed_at",
        "reason_for_manual_review",
    ]
    assert header == expected_columns


def test_generate_manual_review_csv_pending_item_produces_data_row():
    """An unreviewed (pending) HITLReviewItem must appear as a data row."""
    item = _make_hitl_item("hitl-001", reviewer_decision=None)
    csv_output = PDFUABuilder.generate_manual_review_csv([item])
    reader = csv.reader(io.StringIO(csv_output))
    rows = list(reader)
    assert len(rows) == 2  # header + 1 data row


def test_generate_manual_review_csv_rejected_item_produces_data_row():
    """A rejected HITLReviewItem must appear in the CSV output."""
    item = _make_hitl_item("hitl-002", reviewer_decision="reject")
    csv_output = PDFUABuilder.generate_manual_review_csv([item])
    reader = csv.reader(io.StringIO(csv_output))
    rows = list(reader)
    assert len(rows) == 2


def test_generate_manual_review_csv_rejected_item_reason_mentions_rejected():
    """A rejected item's reason_for_manual_review must indicate manual remediation needed."""
    item = _make_hitl_item("hitl-003", reviewer_decision="reject")
    csv_output = PDFUABuilder.generate_manual_review_csv([item])
    assert "reject" in csv_output.lower() or "manual" in csv_output.lower()


def test_generate_manual_review_csv_pending_item_reason_mentions_not_reviewed():
    """A pending item's reason must communicate that review has not yet occurred."""
    item = _make_hitl_item("hitl-004", reviewer_decision=None)
    csv_output = PDFUABuilder.generate_manual_review_csv([item])
    assert "not yet reviewed" in csv_output or "pending" in csv_output.lower()


def test_generate_manual_review_csv_item_id_present_in_output():
    """The item's UUID must appear in the CSV data row."""
    item = _make_hitl_item("hitl-unique-uuid-9999")
    csv_output = PDFUABuilder.generate_manual_review_csv([item])
    assert "hitl-unique-uuid-9999" in csv_output


def test_generate_manual_review_csv_multiple_items_produce_multiple_rows():
    """Multiple HITLReviewItems must each produce a separate CSV row."""
    items = [
        _make_hitl_item("hitl-a", reviewer_decision="reject"),
        _make_hitl_item("hitl-b", reviewer_decision=None),
        _make_hitl_item("hitl-c", reviewer_decision=None),
    ]
    csv_output = PDFUABuilder.generate_manual_review_csv(items)
    reader = csv.reader(io.StringIO(csv_output))
    rows = list(reader)
    assert len(rows) == 4  # 1 header + 3 data rows


# ---------------------------------------------------------------------------
# _validate_heading_sequence (internal helper)
# ---------------------------------------------------------------------------


def test_validate_heading_sequence_empty_list_returns_no_violations():
    """An empty heading list must return no violations."""
    assert _validate_heading_sequence([]) == []


def test_validate_heading_sequence_sequential_levels_returns_no_violations():
    """H1 → H2 → H3 is a valid sequence with no violations."""
    assert _validate_heading_sequence([1, 2, 3]) == []


def test_validate_heading_sequence_h1_to_h3_skip_returns_violation():
    """H1 → H3 skips H2 and must produce at least one violation description."""
    violations = _validate_heading_sequence([1, 3])
    assert len(violations) >= 1


def test_validate_heading_sequence_returning_to_higher_level_is_valid():
    """H3 → H2 (decreasing depth) is always valid and must produce no violations."""
    assert _validate_heading_sequence([1, 2, 3, 2]) == []


def test_validate_heading_sequence_violation_message_mentions_levels():
    """The violation description must mention both the offending level numbers."""
    violations = _validate_heading_sequence([1, 3])
    combined = " ".join(violations)
    assert "1" in combined
    assert "3" in combined
