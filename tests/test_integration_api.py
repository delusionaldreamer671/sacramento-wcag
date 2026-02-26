"""Integration tests for the WCAG pipeline API.

Uses FastAPI TestClient to test the full HTTP request/response cycle.
External services (Adobe, Vertex AI) are not called — these tests
verify route -> handler -> database -> response flow.

Run with:
    pytest tests/test_integration_api.py -v -m integration
"""

from __future__ import annotations

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixture: TestClient backed by an in-memory SQLite database
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create a TestClient with a fresh in-memory database.

    Patches both ``get_db`` call-sites used in main.py and router.py so that
    all request handlers share the same in-memory Database instance, avoiding
    any interaction with the on-disk ``wcag_pipeline.db`` file.
    """
    from services.common.database import Database

    test_db = Database(":memory:")

    # Patch every module that calls get_db so the same in-memory instance is
    # returned for the lifetime of a single test.
    with patch("services.common.database.get_db", return_value=test_db), \
         patch("services.ingestion.router.get_db", return_value=test_db):
        from services.ingestion.main import app
        yield TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Health-check tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_health_check_returns_dependency_status(client):
    """GET /api/health returns 200 with services dict containing required keys.

    The /api/health endpoint is mounted directly on the FastAPI app (outside the
    versioned router prefix), so it remains at /api/health regardless of the
    /api/v1/ prefix used by other routes.
    """
    response = client.get("/api/health")

    assert response.status_code == 200

    body = response.json()
    assert "status" in body
    assert "services" in body

    services = body["services"]
    assert "database" in services, "services dict must contain 'database' key"
    assert "adobe_credentials" in services, "services dict must contain 'adobe_credentials' key"
    assert "vertex_ai" in services, "services dict must contain 'vertex_ai' key"


# ---------------------------------------------------------------------------
# Upload endpoint validation tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upload_rejects_empty_file(client):
    """POST /api/v1/documents/upload with an empty file body returns 400."""
    files = {"file": ("empty.pdf", b"", "application/pdf")}
    response = client.post("/api/v1/documents/upload", files=files)

    assert response.status_code == 400


@pytest.mark.integration
def test_upload_rejects_non_pdf(client):
    """POST /api/v1/documents/upload with a .txt file returns 4xx (400 or 422).

    The API validates both content-type and file extension. A plain-text file
    triggers a 400 Bad Request because the content-type check fires first.
    Any 4xx status is acceptable — the important invariant is rejection.
    """
    files = {"file": ("document.txt", b"not a pdf file content", "text/plain")}
    response = client.post("/api/v1/documents/upload", files=files)

    assert 400 <= response.status_code < 500, (
        f"Expected a 4xx rejection for a .txt file upload, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Analyze endpoint validation tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_analyze_rejects_empty_file(client):
    """POST /api/v1/analyze with an empty file body returns 400."""
    files = {"file": ("empty.pdf", b"", "application/pdf")}
    response = client.post("/api/v1/analyze", files=files)

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# WCAG rules reference endpoints
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_wcag_rules_returns_50_rules(client):
    """GET /api/v1/wcag-rules returns 200 with exactly 50 WCAG 2.1 AA rules."""
    response = client.get("/api/v1/wcag-rules")

    assert response.status_code == 200

    rules = response.json()
    assert isinstance(rules, list), "Response body must be a JSON array"
    assert len(rules) == 50, (
        f"Expected exactly 50 WCAG rules, got {len(rules)}"
    )


@pytest.mark.integration
def test_coverage_matrix_returns_50_entries(client):
    """GET /api/v1/wcag/coverage-matrix returns 200 with exactly 50 entries."""
    response = client.get("/api/v1/wcag/coverage-matrix")

    assert response.status_code == 200

    matrix = response.json()
    assert isinstance(matrix, list), "Coverage matrix must be a JSON array"
    assert len(matrix) == 50, (
        f"Expected exactly 50 coverage matrix entries, got {len(matrix)}"
    )


@pytest.mark.integration
def test_coverage_summary_returns_data(client):
    """GET /api/v1/wcag/coverage-summary returns 200 with a non-empty dict."""
    response = client.get("/api/v1/wcag/coverage-summary")

    assert response.status_code == 200

    body = response.json()
    assert isinstance(body, dict), "Coverage summary must be a JSON object"
    assert len(body) > 0, "Coverage summary must not be empty"


@pytest.mark.integration
def test_content_type_matrix_returns_data(client):
    """GET /api/v1/wcag/content-type-matrix returns 200 with a non-empty list."""
    response = client.get("/api/v1/wcag/content-type-matrix")

    assert response.status_code == 200

    body = response.json()
    assert isinstance(body, list), "Content-type matrix must be a JSON array"
    assert len(body) > 0, "Content-type matrix must not be empty"


# ---------------------------------------------------------------------------
# 404 not-found tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_nonexistent_document_returns_404(client):
    """GET /api/v1/documents/<nonexistent-id> returns 404."""
    response = client.get("/api/v1/documents/nonexistent-id-that-does-not-exist")

    assert response.status_code == 404


@pytest.mark.integration
def test_nonexistent_image_returns_404(client):
    """GET /api/images/<nonexistent-id> returns 404.

    The images endpoint is served directly on the app (outside the /api/v1/ prefix),
    so it remains at /api/images/{image_id}.
    """
    response = client.get("/api/images/nonexistent-image-id-xyz")

    assert response.status_code == 404
