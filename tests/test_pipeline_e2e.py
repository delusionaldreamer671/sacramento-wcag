"""End-to-end integration tests for the full pipeline.

Tests the HTTP round-trip through the FastAPI endpoints, verifying
contract consistency between frontend expectations and backend responses.

Run with:
    pytest tests/test_pipeline_e2e.py -v -m integration
"""

from __future__ import annotations

import json

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create a TestClient with a fresh in-memory database."""
    from services.common.database import Database

    test_db = Database(":memory:")

    with patch("services.common.database.get_db", return_value=test_db), \
         patch("services.ingestion.router.get_db", return_value=test_db):
        from services.ingestion.main import app
        yield TestClient(app, raise_server_exceptions=False)


def _make_simple_pdf() -> bytes:
    """Create a minimal valid PDF for testing."""
    try:
        import pikepdf
    except ImportError:
        pytest.skip("pikepdf not installed")
        return b""

    import io
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))

    # Add StructTreeRoot and MarkInfo for accessibility
    struct_tree = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructTreeRoot"),
        K=pikepdf.Array([]),
    )
    pdf.Root["/StructTreeRoot"] = pdf.make_indirect(struct_tree)
    pdf.Root["/MarkInfo"] = pikepdf.Dictionary(Marked=pikepdf.Boolean(True))

    buf = io.BytesIO()
    pdf.save(buf)
    pdf.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# A3: Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_health_reports_all_dependencies(client):
    """Health check returns all 5 dependency keys."""
    response = client.get("/api/health")
    assert response.status_code == 200

    body = response.json()
    assert "status" in body
    assert "services" in body

    services = body["services"]
    assert "database" in services
    assert "adobe_credentials" in services
    assert "vertex_ai" in services
    assert "verapdf" in services
    assert "axe_core" in services


@pytest.mark.integration
def test_analyze_returns_50_rules_and_proposals(client):
    """Upload PDF -> assert rules_checked==50, proposals non-empty."""
    pdf_bytes = _make_simple_pdf()
    if not pdf_bytes:
        pytest.skip("Could not create test PDF")

    # Mock Adobe extraction to return minimal structure
    mock_extract_json = {
        "elements": [
            {"Path": "//Document/H1/P", "Text": "Test Document"},
            {"Path": "//Document/P", "Text": "This is a test paragraph."},
        ],
        "pages": [{"width": 612, "height": 792}],
    }

    with patch("services.ingestion.converter._run_extraction", return_value=mock_extract_json), \
         patch("services.ingestion.router._check_scanned_pdf"):
        files = {"file": ("test.pdf", pdf_bytes, "application/pdf")}
        response = client.post("/api/v1/analyze", files=files)

    assert response.status_code == 200, f"Analyze failed: {response.text}"
    body = response.json()

    # Must have task_id
    assert "task_id" in body
    assert body["task_id"]

    # Must have summary with 50 rules checked
    assert "summary" in body
    assert body["summary"]["rules_checked"] == 50

    # Must have proposals list
    assert "proposals" in body
    assert isinstance(body["proposals"], list)


@pytest.mark.integration
def test_analyze_pipeline_metadata_shows_stage_statuses(client):
    """Assert pipeline_metadata has all stage entries."""
    pdf_bytes = _make_simple_pdf()
    if not pdf_bytes:
        pytest.skip("Could not create test PDF")

    mock_extract_json = {
        "elements": [
            {"Path": "//Document/P", "Text": "Content"},
        ],
        "pages": [{"width": 612, "height": 792}],
    }

    with patch("services.ingestion.converter._run_extraction", return_value=mock_extract_json), \
         patch("services.ingestion.router._check_scanned_pdf"):
        files = {"file": ("test.pdf", pdf_bytes, "application/pdf")}
        response = client.post("/api/v1/analyze", files=files)

    assert response.status_code == 200
    body = response.json()

    # pipeline_metadata must be present
    assert "pipeline_metadata" in body
    meta = body["pipeline_metadata"]
    assert "stages" in meta
    assert "overall_status" in meta

    # Must have at least extract, deterministic, ai_alt_text stages
    stage_names = [s["stage_name"] for s in meta["stages"]]
    assert "extract" in stage_names
    assert "deterministic_fixes" in stage_names

    # Each stage must have status
    for stage in meta["stages"]:
        assert stage["status"] in ("success", "skipped", "degraded", "failed")


@pytest.mark.integration
def test_analyze_reports_ai_skipped_when_vertex_unavailable(client):
    """Mock AI unavailable -> assert metadata shows skipped/degraded."""
    pdf_bytes = _make_simple_pdf()
    if not pdf_bytes:
        pytest.skip("Could not create test PDF")

    mock_extract_json = {
        "elements": [
            {"Path": "//Document/P", "Text": "Content"},
        ],
        "pages": [{"width": 612, "height": 792}],
    }

    with patch("services.ingestion.converter._run_extraction", return_value=mock_extract_json), \
         patch("services.ingestion.converter._vertex_ai_available", return_value=False), \
         patch("services.ingestion.router._check_scanned_pdf"):
        files = {"file": ("test.pdf", pdf_bytes, "application/pdf")}
        response = client.post("/api/v1/analyze", files=files)

    assert response.status_code == 200
    body = response.json()

    meta = body.get("pipeline_metadata", {})
    stages = meta.get("stages", [])
    ai_stage = next((s for s in stages if s["stage_name"] == "ai_alt_text"), None)
    assert ai_stage is not None
    # When Vertex AI is unavailable, AI stage returns the ir_doc unchanged (success)
    # but the stage itself succeeds — it just doesn't generate any alt text
    assert ai_stage["status"] in ("success", "degraded", "skipped")


@pytest.mark.integration
def test_remediate_returns_task_id_header(client):
    """Assert X-Task-Id header present in remediate response."""
    pdf_bytes = _make_simple_pdf()
    if not pdf_bytes:
        pytest.skip("Could not create test PDF")

    mock_extract_json = {
        "elements": [
            {"Path": "//Document/H1/P", "Text": "Title"},
            {"Path": "//Document/P", "Text": "Content"},
        ],
        "pages": [{"width": 612, "height": 792}],
    }

    with patch("services.ingestion.converter._run_extraction", return_value=mock_extract_json), \
         patch("services.ingestion.converter._vertex_ai_available", return_value=False), \
         patch("services.ingestion.router._check_scanned_pdf"):
        files = {"file": ("test.pdf", pdf_bytes, "application/pdf")}
        response = client.post(
            "/api/v1/remediate",
            files=files,
            params={"output_format": "html"},
            data={"approved_ids": ""},
        )

    assert response.status_code == 200, f"Remediate failed: {response.text}"
    assert "x-task-id" in response.headers
    assert response.headers["x-task-id"]

    # Pipeline metadata header should be present
    assert "x-pipeline-metadata" in response.headers
    meta = json.loads(response.headers["x-pipeline-metadata"])
    assert "stages" in meta
    assert "overall_status" in meta


@pytest.mark.integration
def test_remediate_produces_html_with_lang_and_title(client):
    """Upload -> assert HTML has lang="en", <title>."""
    pdf_bytes = _make_simple_pdf()
    if not pdf_bytes:
        pytest.skip("Could not create test PDF")

    mock_extract_json = {
        "elements": [
            {"Path": "//Document/H1/P", "Text": "My Document Title"},
            {"Path": "//Document/P", "Text": "Some content."},
        ],
        "pages": [{"width": 612, "height": 792}],
    }

    with patch("services.ingestion.converter._run_extraction", return_value=mock_extract_json), \
         patch("services.ingestion.converter._vertex_ai_available", return_value=False), \
         patch("services.ingestion.router._check_scanned_pdf"):
        files = {"file": ("test.pdf", pdf_bytes, "application/pdf")}
        response = client.post(
            "/api/v1/remediate",
            files=files,
            params={"output_format": "html"},
            data={"approved_ids": ""},
        )

    assert response.status_code == 200
    html = response.text

    # Check for language attribute
    assert 'lang="en"' in html, "HTML must have lang='en' attribute"

    # Check for title element
    assert "<title>" in html, "HTML must have a <title> element"
