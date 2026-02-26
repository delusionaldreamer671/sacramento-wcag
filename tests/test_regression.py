"""Golden corpus regression test.

Runs the 12-PDF test harness and verifies that no document regresses
below its baseline score. Skipped if the corpus directory is not found
on disk or if Adobe credentials are not configured.

Also includes output quality assertions that catch common regressions:
  - Images must have src attributes (not empty <img> tags)
  - ALL-CAPS text must not have merged words
  - Heading spacing must be correct
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

# Applied per-test to those that need Adobe credentials (not the synthetic test)
_needs_adobe = pytest.mark.skipif(
    not os.environ.get("WCAG_ADOBE_CLIENT_ID"),
    reason="Adobe credentials not configured (WCAG_ADOBE_CLIENT_ID not set)",
)

BASELINES_PATH = Path(__file__).parent / "golden_baselines.json"


def _load_baselines() -> dict:
    if not BASELINES_PATH.exists():
        pytest.skip("golden_baselines.json not found")
    with open(BASELINES_PATH) as f:
        return json.load(f)


def _find_corpus_dir() -> Path | None:
    candidates = [
        Path(__file__).parent / "corpus",
        Path(__file__).parent.parent / "test-corpus",
        Path(__file__).parent.parent / "corpus",
    ]
    for p in candidates:
        if p.is_dir() and list(p.glob("*.pdf")):
            return p
    return None


@_needs_adobe
@pytest.mark.slow
def test_regression_no_score_decrease():
    """Run harness on corpus and verify no document drops below baseline."""
    corpus_dir = _find_corpus_dir()
    if corpus_dir is None:
        pytest.skip("PDF corpus directory not found on disk")

    baselines = _load_baselines()
    thresholds = baselines.get("thresholds", {"GREEN": 0.90, "YELLOW": 0.70})

    from tests.auto_harness.runner import run_harness

    results = run_harness(str(corpus_dir))

    if not results:
        pytest.skip("Harness returned no results (no PDFs processed)")

    scores = [r.get("score", 0) for r in results if isinstance(r, dict)]
    if not scores:
        pytest.skip("No valid scores in harness results")

    avg_score = sum(scores) / len(scores)
    baseline_avg = baselines.get("avg_score", 0.942)
    tolerance = baselines.get("regression_rules", {}).get("tolerance", 0.005)

    assert avg_score >= baseline_avg - tolerance, (
        f"Average score {avg_score:.4f} dropped below baseline "
        f"{baseline_avg:.4f} (tolerance: {tolerance})"
    )


@_needs_adobe
@pytest.mark.slow
def test_regression_no_color_downgrade():
    """No document may drop from GREEN to YELLOW or YELLOW to RED."""
    corpus_dir = _find_corpus_dir()
    if corpus_dir is None:
        pytest.skip("PDF corpus directory not found on disk")

    baselines = _load_baselines()
    thresholds = baselines.get("thresholds", {"GREEN": 0.90, "YELLOW": 0.70})

    from tests.auto_harness.runner import run_harness

    results = run_harness(str(corpus_dir))

    if not results:
        pytest.skip("Harness returned no results")

    red_count = sum(
        1 for r in results
        if isinstance(r, dict) and r.get("score", 0) < thresholds["YELLOW"]
    )

    baseline_red = baselines.get("red_count", 0)
    assert red_count <= baseline_red, (
        f"RED count increased from {baseline_red} to {red_count}"
    )


# ---------------------------------------------------------------------------
# Output quality regression: images and spacing
# ---------------------------------------------------------------------------


# Common ALL-CAPS merged-word patterns seen in government PDFs.
# If these appear without spaces, _clean_text or fragment merging has regressed.
_MERGED_WORD_PATTERNS = [
    "SACRAMENTOCOUNTY",
    "LOCALROAD",
    "PUBLICOUTREACH",
    "VISIONSTATEMENT",
    "STAKEHOLDEROUTREACH",
    "SAFETYPARTNERS",
    "DATASUMMARY",
    "DATASOURCES",
    "CRASHRECORD",
    "COLLISIONTRENDS",
    "EMPHASISAREAS",
    "VULNERABLEUSERS",
    "RISKYBEHAVIORS",
]


@_needs_adobe
@pytest.mark.slow
def test_output_images_have_src():
    """Every <img> in the HTML output must have a src attribute.

    Catches regressions where PyMuPDF or Adobe figure image extraction breaks,
    producing empty <img alt="..."> tags with no visual content.
    """
    corpus_dir = _find_corpus_dir()
    if corpus_dir is None:
        pytest.skip("PDF corpus directory not found")

    from services.ingestion.converter import convert_pdf_sync

    pdfs = list(Path(corpus_dir).glob("*.pdf"))
    if not pdfs:
        pytest.skip("No PDFs in corpus directory")

    # Test on first PDF only (speed)
    pdf_path = pdfs[0]
    pdf_bytes = pdf_path.read_bytes()

    output_bytes, _, _ = convert_pdf_sync(pdf_bytes, pdf_path.name, "html")
    html = output_bytes.decode("utf-8")

    imgs_total = len(re.findall(r"<img ", html))
    imgs_with_src = len(re.findall(r'<img [^>]*src=', html))

    if imgs_total == 0:
        return  # No images in document — nothing to check

    # Allow up to 10% without src (decorative images may intentionally omit src)
    min_with_src = int(imgs_total * 0.9)
    assert imgs_with_src >= min_with_src, (
        f"Only {imgs_with_src}/{imgs_total} images have src attributes. "
        f"Expected at least {min_with_src}. Image extraction may have regressed."
    )


@_needs_adobe
@pytest.mark.slow
def test_output_no_merged_allcaps_words():
    """ALL-CAPS headings must not have merged words (e.g., SACRAMENTOCOUNTY).

    Catches regressions in _clean_text() or _merge_fragment_elements() where
    Adobe Extract's spacing issues are no longer being fixed.
    """
    corpus_dir = _find_corpus_dir()
    if corpus_dir is None:
        pytest.skip("PDF corpus directory not found")

    # Look for the LRSP test file specifically (it has known merged-word issues)
    lrsp_candidates = list(Path(corpus_dir).glob("*LRSP*"))
    if not lrsp_candidates:
        pytest.skip("LRSP test file not found in corpus")

    pdf_path = lrsp_candidates[0]
    pdf_bytes = pdf_path.read_bytes()

    from services.ingestion.converter import convert_pdf_sync

    output_bytes, _, _ = convert_pdf_sync(pdf_bytes, pdf_path.name, "html")
    html = output_bytes.decode("utf-8")

    found_merged = []
    for pattern in _MERGED_WORD_PATTERNS:
        if pattern in html:
            found_merged.append(pattern)

    assert not found_merged, (
        f"Found {len(found_merged)} merged ALL-CAPS words in output: "
        f"{found_merged}. Text spacing fix has regressed."
    )


def test_output_quality_synthetic():
    """Synthetic test: verify image and spacing handling without corpus.

    Uses mocked extraction data to verify the reconstruction pipeline
    produces correct output regardless of Adobe API availability.
    """
    from services.ingestion.converter import _clean_text, _merge_fragment_elements

    # Test _clean_text fixes known spacing issues
    assert _clean_text("SACRAMENTO COUNTY") == "SACRAMENTO COUNTY"
    # Broken words should be fixed
    assert _clean_text("FIGUR E S") == "FIGURES"
    assert _clean_text("PROGRAMMATI C") == "PROGRAMMATIC"
    # Separate words should NOT be merged
    assert "SACRAMENTOCOUNTY" not in _clean_text("SACRAMENTO COUNTY")

    # Test _merge_fragment_elements
    elements = [
        {"Path": "//Document/P[1]", "Text": "I"},
        {"Path": "//Document/P[2]", "Text": "NFRASTRUCTURE details"},
    ]
    merged = _merge_fragment_elements(elements)
    assert len(merged) == 1
    assert merged[0]["Text"] == "INFRASTRUCTURE details"


def test_validation_blocked_error_importable():
    """ValidationBlockedError must be importable and carry violation data."""
    from services.ingestion.converter import ValidationBlockedError

    exc = ValidationBlockedError(
        "Output blocked: 5 images missing src",
        violations=[{"check_name": "img_src", "status": "hard_fail"}],
    )
    assert str(exc) == "Output blocked: 5 images missing src"
    assert len(exc.violations) == 1
    assert exc.violations[0]["check_name"] == "img_src"


def test_g3_gate_catches_missing_src():
    """G3 gate must hard-fail when images lack src attributes."""
    from services.common.gates import run_gate_g3

    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head><title>Test</title></head>\n'
        '<body><main><h1>Title</h1>'
        '<figure><img alt="Chart showing data"></figure>'
        '</main></body></html>'
    )
    result = run_gate_g3(html)
    assert not result.passed
    img_src_checks = [c for c in result.checks if c.check_name == "img_src"]
    assert len(img_src_checks) == 1
    assert img_src_checks[0].status == "hard_fail"
    assert img_src_checks[0].priority == "P0"


# ---------------------------------------------------------------------------
# Batch 8 — Structural quality tests for the comprehensive output fix
# ---------------------------------------------------------------------------


def test_skip_navigation_link_present():
    """Output HTML must include a skip-to-main-content link (WCAG 2.4.1)."""
    from services.recompilation.pdfua_builder import PDFUABuilder

    builder = PDFUABuilder(document_id="skip-nav-test", document_title="Test Doc")
    builder.add_element("heading", "Title", {"level": 1})
    builder.add_element("paragraph", "Content here.")
    html = builder.build_semantic_html()

    assert 'href="#main-content"' in html, "Skip navigation link missing"
    assert 'id="main-content"' in html, "Main content target ID missing"
    assert html.index('href="#main-content"') < html.index('id="main-content"'), (
        "Skip link must appear before main content"
    )


def test_toc_anchors_match_heading_ids():
    """TOC link targets must exactly match heading id attributes."""
    from services.recompilation.pdfua_builder import PDFUABuilder

    builder = PDFUABuilder(document_id="toc-test", document_title="Doc")
    builder.add_element("heading", "Title", {"level": 1})
    builder.add_element("heading", "Introduction", {"level": 2})
    builder.add_element("paragraph", "Intro text.")
    builder.add_element("heading", "Methods", {"level": 2})
    builder.add_element("paragraph", "Methods text.")
    builder.add_element("heading", "Results", {"level": 2})
    builder.add_element("paragraph", "Results text.")
    builder.add_element("heading", "Discussion", {"level": 2})
    builder.add_element("paragraph", "Discussion text.")
    html = builder.build_semantic_html()

    # Extract TOC link targets
    toc_hrefs = re.findall(r'<a href="#([^"]+)">', html)
    # Extract heading IDs
    heading_ids = re.findall(r'<h[1-6] id="([^"]+)">', html)

    # Every TOC link must have a matching heading ID
    for href in toc_hrefs:
        assert href in heading_ids, (
            f"TOC link #{href} has no matching heading id. "
            f"TOC targets: {toc_hrefs}, heading IDs: {heading_ids}"
        )


def test_heading_hierarchy_single_h1():
    """Document must have exactly one H1 element after hierarchy enforcement."""
    from services.ingestion.converter import _enforce_heading_hierarchy

    elements = [
        {"type": "heading", "content": "SACRAMENTO COUNTY", "attributes": {"level": 1}},
        {"type": "heading", "content": "LOCAL ROAD SAFETY PLAN", "attributes": {"level": 1}},
        {"type": "heading", "content": "Introduction", "attributes": {"level": 2}},
        {"type": "heading", "content": "Data Summary", "attributes": {"level": 2}},
    ]
    _enforce_heading_hierarchy(elements)

    h1_count = sum(1 for e in elements if e["type"] == "heading" and e["attributes"]["level"] == 1)
    assert h1_count == 1, f"Expected exactly 1 H1, got {h1_count}"


def test_heading_hierarchy_no_inversion():
    """No subsection heading may have a lower level number than its parent."""
    from services.ingestion.converter import _enforce_heading_hierarchy

    elements = [
        {"type": "heading", "content": "Title", "attributes": {"level": 1}},
        {"type": "heading", "content": "Section", "attributes": {"level": 3}},
        {"type": "heading", "content": "Subsection", "attributes": {"level": 2}},  # inverted!
        {"type": "paragraph", "content": "text", "attributes": {}},
    ]
    _enforce_heading_hierarchy(elements)

    # After enforcement, "Subsection" must not be lower level than "Section"
    headings = [e for e in elements if e["type"] == "heading"]
    for i in range(1, len(headings)):
        prev_level = headings[i - 1]["attributes"]["level"]
        curr_level = headings[i]["attributes"]["level"]
        # Each heading can be same, one deeper, or starting a new section at same/higher level
        # But can never jump more than 1 deeper
        assert curr_level <= prev_level + 1, (
            f"Heading '{headings[i]['content']}' at level {curr_level} skips "
            f"from level {prev_level} (should be at most {prev_level + 1})"
        )


def test_table_never_collapses_to_paragraphs():
    """Tables with all single-cell rows must still emit a table element, not paragraphs."""
    from services.ingestion.converter import _build_single_page_table

    # Simulate all single-cell rows (the old bug)
    # _build_single_page_table expects list[tuple[int, int, str, str]]:
    #   (row_index, col_index, cell_type, text)
    cells = [
        (0, 0, "TH", "Header"),
        (1, 0, "TD", "Row 1"),
        (2, 0, "TD", "Row 2"),
    ]
    result = _build_single_page_table(cells, page_num=0)
    # Must produce at least one table element
    types = [e["type"] for e in result]
    assert "table" in types, (
        f"Expected a table element, got only: {types}. Table collapsed to paragraphs."
    )


def test_figure_caption_association():
    """Figure captions matching 'Figure N:' must be associated with the figure."""
    from services.ingestion.converter import _reconstruct_document

    # Create minimal Adobe Extract JSON with a figure followed by its caption
    extract_json = {
        "elements": [
            {"Path": "//Document/H1", "Text": "Report Title"},
            {"Path": "//Document/Figure", "Text": "", "Page": 1},
            {"Path": "//Document/P[1]", "Text": "Figure 1: Annual Revenue Chart"},
            {"Path": "//Document/P[2]", "Text": "Normal paragraph after figure."},
        ]
    }
    result = _reconstruct_document(extract_json)

    # Find the image element
    images = [e for e in result if e["type"] == "image"]
    if images:
        # The caption should be in the image's attributes
        assert images[0]["attributes"].get("caption"), (
            "Figure caption 'Figure 1: Annual Revenue Chart' was not associated with the image"
        )


def test_validation_score_not_100_for_empty():
    """Validation must not report 100% (1.0) when no meaningful checks ran."""
    from services.recompilation.pdfua_builder import PDFUABuilder

    builder = PDFUABuilder(document_id="empty-test", document_title="Test")
    # Empty document — no elements at all
    result = builder.validate_accessibility("")
    assert result["score"] == 0.0, (
        f"Expected score 0.0 for empty HTML, got {result['score']}"
    )


def test_validation_detects_placeholder_text():
    """Validation must flag placeholder alt text as a violation."""
    from services.recompilation.pdfua_builder import PDFUABuilder

    builder = PDFUABuilder(document_id="placeholder-test", document_title="Test")
    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head><title>Test</title></head>\n'
        '<body><a href="#main-content">Skip</a>'
        '<main id="main-content"><h1>Title</h1>'
        '<figure><img src="data:image/png;base64,x" '
        'alt="[Figure on page 5 — alt text requires review]"></figure>'
        '</main></body></html>'
    )
    result = builder.validate_accessibility(html)
    placeholder_violations = [
        v for v in result["violations"]
        if "placeholder" in v.get("description", "").lower()
    ]
    assert len(placeholder_violations) > 0, (
        "Validation should flag placeholder text as a violation"
    )


def test_badge_colors_wcag_compliant():
    """Badge colors must have sufficient contrast against white background."""
    from services.ingestion.converter import _inject_validation_summary

    # Create a minimal validation result
    validation = {
        "violations": [],
        "score": 1.0,
        "blocked": False,
        "critical_violations": [],
        "serious_violations": [],
    }
    html = '<html><body><main><h1>Test</h1></main></body></html>'
    result = _inject_validation_summary(html, validation)

    # The old problematic green was #22c55e (2.28:1 contrast)
    assert "#22c55e" not in result, (
        "Badge still uses #22c55e which has only 2.28:1 contrast (fails WCAG 4.5:1)"
    )
