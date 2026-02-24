"""End-to-end WCAG validation tests using PDFUABuilder.

These tests exercise the full build_semantic_html → validate_accessibility
pipeline, verifying that the final HTML output satisfies each targeted
WCAG 2.1 AA criterion. No external services are called.
"""

from __future__ import annotations

import re

import pytest

from services.recompilation.pdfua_builder import PDFUABuilder


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_builder() -> PDFUABuilder:
    """A fresh PDFUABuilder for each test."""
    return PDFUABuilder(
        document_id="e2e-test-doc",
        document_title="Sacramento County Test Document",
        language="en",
    )


# ---------------------------------------------------------------------------
# Complete document validation
# ---------------------------------------------------------------------------


def test_complete_document_produces_valid_html(clean_builder: PDFUABuilder):
    """A document built from all element types must produce well-formed HTML with no missing tags."""
    clean_builder.add_element("heading", "Report Title", {"level": 1})
    clean_builder.add_element("paragraph", "Introduction.")
    clean_builder.add_element("image", "", {"alt": "County logo"})
    clean_builder.add_element(
        "table",
        "Budget",
        {"headers": ["Dept", "Amount"], "rows": [["IT", "$1M"]]},
    )
    html = clean_builder.build_semantic_html()

    # Structural completeness
    assert "<!DOCTYPE html>" in html
    assert "<html" in html and "</html>" in html
    assert "<head>" in html and "</head>" in html
    assert "<body>" in html and "</body>" in html
    assert "<main" in html and "</main>" in html


def test_document_with_all_element_types_passes_validation(clean_builder: PDFUABuilder):
    """A document containing every supported element type must pass all accessibility checks."""
    clean_builder.add_element("heading", "Sacramento County Annual Report", {"level": 1})
    clean_builder.add_element("heading", "Section 1: Budget", {"level": 2})
    clean_builder.add_element("paragraph", "Budget details follow.")
    clean_builder.add_element(
        "image", "", {"alt": "Bar chart of 2025 budget allocation by department", "src": "data:image/png;base64,iVBOR"}
    )
    clean_builder.add_element(
        "table",
        "Expenditure Summary",
        {
            "headers": ["Department", "Allocated", "Spent"],
            "rows": [
                ["Public Safety", "$12M", "$11.2M"],
                ["Health", "$8M", "$7.8M"],
            ],
        },
    )
    clean_builder.add_element(
        "list", "", {"items": ["Objective A", "Objective B"], "ordered": False}
    )
    clean_builder.add_element(
        "link", "View full report", {"href": "https://saccounty.gov/report"}
    )

    html = clean_builder.build_semantic_html()
    report = clean_builder.validate_accessibility(html)

    assert report["violations"] == [], (
        f"Expected zero violations but got: {report['violations']}"
    )
    assert report["score"] == 1.0


# ---------------------------------------------------------------------------
# Reading order (WCAG 1.3.2)
# ---------------------------------------------------------------------------


def test_reading_order_matches_insertion_order(clean_builder: PDFUABuilder):
    """Elements must appear in the HTML in the same order they were added to the builder."""
    clean_builder.add_element("heading", "First Heading", {"level": 1})
    clean_builder.add_element("paragraph", "First Paragraph")
    clean_builder.add_element("heading", "Second Heading", {"level": 2})
    clean_builder.add_element("paragraph", "Second Paragraph")

    html = clean_builder.build_semantic_html()

    first_heading_pos = html.index("First Heading")
    first_para_pos = html.index("First Paragraph")
    second_heading_pos = html.index("Second Heading")
    second_para_pos = html.index("Second Paragraph")

    assert first_heading_pos < first_para_pos
    assert first_para_pos < second_heading_pos
    assert second_heading_pos < second_para_pos


def test_multiple_images_appear_in_insertion_order(clean_builder: PDFUABuilder):
    """Multiple image elements must appear in the HTML in the order they were added."""
    clean_builder.add_element("image", "Caption A", {"alt": "First image", "src": "a.png"})
    clean_builder.add_element("image", "Caption B", {"alt": "Second image", "src": "b.png"})

    html = clean_builder.build_semantic_html()

    first_pos = html.index("First image")
    second_pos = html.index("Second image")
    assert first_pos < second_pos


# ---------------------------------------------------------------------------
# Language attribute (WCAG 3.1.1)
# ---------------------------------------------------------------------------


def test_language_attribute_present_in_output(clean_builder: PDFUABuilder):
    """The output HTML must have a non-empty lang attribute on <html> (WCAG 3.1.1)."""
    html = clean_builder.build_semantic_html()
    match = re.search(r'<html[^>]+lang=["\']([^"\']+)["\']', html, re.IGNORECASE)
    assert match is not None, "<html> tag is missing a lang attribute"
    assert match.group(1).strip() != ""


def test_language_attribute_value_matches_builder_language():
    """The lang attribute value must match the language passed to PDFUABuilder."""
    builder = PDFUABuilder(
        document_id="lang-test-001",
        document_title="French Document",
        language="fr",
    )
    html = builder.build_semantic_html()
    assert 'lang="fr"' in html


def test_language_attribute_defaults_to_english_when_not_specified():
    """When no language is specified, the builder must default to 'en'."""
    builder = PDFUABuilder(document_id="lang-default-001", document_title="Default Language")
    html = builder.build_semantic_html()
    assert 'lang="en"' in html


# ---------------------------------------------------------------------------
# Table headers with scope (WCAG 1.3.1)
# ---------------------------------------------------------------------------


def test_table_headers_have_scope_in_output(clean_builder: PDFUABuilder):
    """All <th> elements produced by the builder must carry a scope attribute (WCAG 1.3.1)."""
    clean_builder.add_element(
        "table",
        "Revenue Data",
        {"headers": ["Year", "Revenue", "Growth"], "rows": [["2025", "$5M", "12%"]]},
    )
    html = clean_builder.build_semantic_html()

    th_tags = re.findall(r"<th\b([^>]*)>", html, re.IGNORECASE)
    assert len(th_tags) > 0, "Expected at least one <th> tag in output"
    for th_attrs in th_tags:
        assert "scope=" in th_attrs, f"<th> missing scope attribute: <th{th_attrs}>"


def test_table_with_multiple_headers_all_have_scope(clean_builder: PDFUABuilder):
    """Every column header cell must have scope='col' when the table has multiple headers."""
    clean_builder.add_element(
        "table",
        "",
        {
            "headers": ["A", "B", "C", "D"],
            "rows": [["1", "2", "3", "4"], ["5", "6", "7", "8"]],
        },
    )
    html = clean_builder.build_semantic_html()
    th_tags = re.findall(r"<th\b([^>]*)>", html, re.IGNORECASE)
    assert len(th_tags) == 4
    for attrs in th_tags:
        assert 'scope="col"' in attrs


def test_table_data_rows_use_td_not_th(clean_builder: PDFUABuilder):
    """Purely numeric data cells in table body rows must use <td>, not <th>."""
    clean_builder.add_element(
        "table",
        "",
        {"headers": ["X", "Y"], "rows": [["1", "10"], ["2", "20"]]},
    )
    html = clean_builder.build_semantic_html()
    tbody_match = re.search(r"<tbody>(.*?)</tbody>", html, re.IGNORECASE | re.DOTALL)
    assert tbody_match is not None, "Expected a <tbody> section"
    tbody_content = tbody_match.group(1)
    assert "<th" not in tbody_content.lower()


def test_table_row_headers_for_textual_first_cell(clean_builder: PDFUABuilder):
    """When the first cell of a body row is a text label, render as <th scope="row">."""
    clean_builder.add_element(
        "table",
        "",
        {"headers": ["Name", "Value"], "rows": [["Alpha", "10"], ["Beta", "20"]]},
    )
    html = clean_builder.build_semantic_html()
    tbody_match = re.search(r"<tbody>(.*?)</tbody>", html, re.IGNORECASE | re.DOTALL)
    assert tbody_match is not None, "Expected a <tbody> section"
    tbody_content = tbody_match.group(1)
    # The first cell of each row should be a row header
    row_headers = re.findall(r'<th\s+scope="row"[^>]*>([^<]+)</th>', tbody_content)
    assert len(row_headers) == 2, f"Expected 2 row headers, got {len(row_headers)}"
    assert "Alpha" in row_headers
    assert "Beta" in row_headers
    # The second cell of each row should still be <td>
    td_cells = re.findall(r"<td>([^<]+)</td>", tbody_content)
    assert "10" in td_cells
    assert "20" in td_cells


# ---------------------------------------------------------------------------
# Alt text on images (WCAG 1.1.1)
# ---------------------------------------------------------------------------


def test_images_have_alt_in_output(clean_builder: PDFUABuilder):
    """All <img> elements in the output must have a non-empty alt attribute (WCAG 1.1.1)."""
    clean_builder.add_element(
        "image", "", {"alt": "Descriptive alt text for this figure"}
    )
    html = clean_builder.build_semantic_html()

    img_tags = re.findall(r"<img\b([^>]*)>", html, re.IGNORECASE)
    assert len(img_tags) > 0, "Expected at least one <img> tag"
    for img_attrs in img_tags:
        alt_match = re.search(r'alt=["\']([^"\']*)["\']', img_attrs)
        assert alt_match is not None, f"<img> missing alt attribute: <img{img_attrs}>"
        assert alt_match.group(1).strip() != "", "<img> has empty alt attribute"


def test_multiple_images_all_have_alt(clean_builder: PDFUABuilder):
    """When multiple images are added, every resulting <img> must have a non-empty alt."""
    alts = [
        "First image description",
        "Second image description",
        "Third image description",
    ]
    for alt in alts:
        clean_builder.add_element("image", "", {"alt": alt})

    html = clean_builder.build_semantic_html()
    for alt in alts:
        assert f'alt="{alt}"' in html


# ---------------------------------------------------------------------------
# Heading hierarchy (WCAG 2.4.6)
# ---------------------------------------------------------------------------


def test_heading_hierarchy_no_skipped_levels(clean_builder: PDFUABuilder):
    """Sequential H1 → H2 → H3 headings must produce zero 2.4.6 violations."""
    clean_builder.add_element("heading", "Title", {"level": 1})
    clean_builder.add_element("heading", "Section", {"level": 2})
    clean_builder.add_element("heading", "Subsection", {"level": 3})

    html = clean_builder.build_semantic_html()
    report = clean_builder.validate_accessibility(html)

    heading_violations = [v for v in report["violations"] if v["criterion"] == "2.4.6"]
    assert heading_violations == []


def test_single_h1_heading_produces_no_hierarchy_violations(clean_builder: PDFUABuilder):
    """A document with a single H1 is a valid hierarchy with no violations."""
    clean_builder.add_element("heading", "Only Heading", {"level": 1})
    html = clean_builder.build_semantic_html()
    report = clean_builder.validate_accessibility(html)
    heading_violations = [v for v in report["violations"] if v["criterion"] == "2.4.6"]
    assert heading_violations == []


def test_heading_level_defaults_to_two_when_not_specified(clean_builder: PDFUABuilder):
    """When no level attribute is supplied, the builder must default to H2."""
    clean_builder.add_element("heading", "Default Level Heading", {})
    html = clean_builder.build_semantic_html()
    assert "<h2" in html
    assert "Default Level Heading</h2>" in html


# ---------------------------------------------------------------------------
# Minimal / edge-case documents
# ---------------------------------------------------------------------------


def test_empty_document_produces_minimal_valid_output(clean_builder: PDFUABuilder):
    """An empty builder must produce a structurally valid HTML document."""
    html = clean_builder.build_semantic_html()
    assert "<!DOCTYPE html>" in html
    assert 'lang="en"' in html
    assert "<title>" in html
    assert "</html>" in html


def test_empty_document_passes_all_validation_checks(clean_builder: PDFUABuilder):
    """An empty document has no images, tables, or headings — all checks pass vacuously."""
    html = clean_builder.build_semantic_html()
    report = clean_builder.validate_accessibility(html)
    # No checkable elements means no violations
    assert report["violations"] == []


def test_document_title_appears_in_title_element(clean_builder: PDFUABuilder):
    """The document_title must appear inside a <title> element in the <head>."""
    html = clean_builder.build_semantic_html()
    assert "<title>Sacramento County Test Document</title>" in html


def test_html_escaping_in_content(clean_builder: PDFUABuilder):
    """HTML special characters in content must be escaped to prevent injection."""
    clean_builder.add_element("paragraph", "<script>alert('xss')</script>")
    html = clean_builder.build_semantic_html()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_html_escaping_in_alt_text(clean_builder: PDFUABuilder):
    """HTML special characters in alt text must be escaped."""
    clean_builder.add_element("image", "", {"alt": 'Image with "quotes" & <tags>'})
    html = clean_builder.build_semantic_html()
    # The raw unescaped form must not appear in the alt attribute value
    assert "'Image with \"quotes\" & <tags>'" not in html
    assert "&amp;" in html or "&lt;" in html or "&quot;" in html
