"""Tests for the require_auth pipeline endpoint dependency.

Covers:
  - Bypass mode: no tokens configured → all requests allowed
  - Auth configured: valid admin token → allowed
  - Auth configured: valid reviewer token → allowed
  - Auth configured: missing Authorization header → 401
  - Auth configured: malformed header (no Bearer scheme) → 401
  - Auth configured: wrong token → 401
  - Auth configured: correct token format but mismatched value → 401
  - Integration: protected endpoints return 401 without token when auth is configured
  - Integration: protected endpoints pass when valid token provided
  - Integration: public endpoints remain accessible without a token
"""

from __future__ import annotations

import asyncio
from typing import Optional
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Unit tests for require_auth dependency (no HTTP server)
# ---------------------------------------------------------------------------


class TestRequireAuthUnit:
    """Direct unit tests for the require_auth async dependency function."""

    def _run(self, coro):
        """Run an async coroutine synchronously in the current event loop."""
        return asyncio.get_event_loop().run_until_complete(coro)

    # -- Bypass mode ----------------------------------------------------------

    def test_bypass_when_both_tokens_empty(self):
        """If no tokens are configured, require_auth must allow any request."""
        from services.common.auth import require_auth
        from fastapi import HTTPException

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = ""
            mock_settings.reviewer_token = ""
            # Should not raise
            result = self._run(require_auth(authorization=None))
            assert result is None  # returns None on success

    def test_bypass_allows_any_header_value_when_no_tokens(self):
        """Bypass mode ignores whatever header value is present."""
        from services.common.auth import require_auth

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = ""
            mock_settings.reviewer_token = ""
            # Even a completely wrong header is fine in bypass mode
            result = self._run(require_auth(authorization="garbage-value"))
            assert result is None

    # -- Auth configured, valid tokens ----------------------------------------

    def test_valid_admin_token_passes(self):
        """A correct admin token in Bearer format is accepted."""
        from services.common.auth import require_auth

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = "secret-admin-token"
            mock_settings.reviewer_token = "secret-reviewer-token"
            # Should not raise
            result = self._run(require_auth(authorization="Bearer secret-admin-token"))
            assert result is None

    def test_valid_reviewer_token_passes(self):
        """A correct reviewer token in Bearer format is accepted."""
        from services.common.auth import require_auth

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = "secret-admin-token"
            mock_settings.reviewer_token = "secret-reviewer-token"
            result = self._run(require_auth(authorization="Bearer secret-reviewer-token"))
            assert result is None

    def test_only_admin_token_configured_reviewer_empty(self):
        """When only admin_token is set, the admin token is valid and anything else is rejected."""
        from services.common.auth import require_auth
        from fastapi import HTTPException

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = "only-admin"
            mock_settings.reviewer_token = ""
            # Admin token works
            result = self._run(require_auth(authorization="Bearer only-admin"))
            assert result is None

            # Random token is rejected
            with pytest.raises(HTTPException) as exc_info:
                self._run(require_auth(authorization="Bearer random-token"))
            assert exc_info.value.status_code == 401

    def test_only_reviewer_token_configured_admin_empty(self):
        """When only reviewer_token is set, the reviewer token is valid."""
        from services.common.auth import require_auth
        from fastapi import HTTPException

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = ""
            mock_settings.reviewer_token = "only-reviewer"
            # Reviewer token works — but not bypass mode because reviewer_token is set
            result = self._run(require_auth(authorization="Bearer only-reviewer"))
            assert result is None

            # No header → 401 (auth IS configured)
            with pytest.raises(HTTPException) as exc_info:
                self._run(require_auth(authorization=None))
            assert exc_info.value.status_code == 401

    # -- Auth configured, failures --------------------------------------------

    def test_missing_authorization_header_raises_401(self):
        """No Authorization header → 401 when auth is configured."""
        from services.common.auth import require_auth
        from fastapi import HTTPException

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = "admin-tok"
            mock_settings.reviewer_token = "reviewer-tok"
            with pytest.raises(HTTPException) as exc_info:
                self._run(require_auth(authorization=None))
            assert exc_info.value.status_code == 401
            assert "WWW-Authenticate" in exc_info.value.headers
            assert exc_info.value.headers["WWW-Authenticate"] == "Bearer"

    def test_missing_authorization_header_error_message(self):
        """401 response includes a human-readable explanation."""
        from services.common.auth import require_auth
        from fastapi import HTTPException

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = "admin-tok"
            mock_settings.reviewer_token = "reviewer-tok"
            with pytest.raises(HTTPException) as exc_info:
                self._run(require_auth(authorization=None))
            assert "Authentication required" in exc_info.value.detail

    def test_malformed_header_no_bearer_raises_401(self):
        """Authorization header without 'Bearer' scheme → 401."""
        from services.common.auth import require_auth
        from fastapi import HTTPException

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = "admin-tok"
            mock_settings.reviewer_token = "reviewer-tok"
            with pytest.raises(HTTPException) as exc_info:
                self._run(require_auth(authorization="Basic dXNlcjpwYXNz"))
            assert exc_info.value.status_code == 401

    def test_bearer_with_empty_token_raises_401(self):
        """'Bearer ' with no token after the space → 401."""
        from services.common.auth import require_auth
        from fastapi import HTTPException

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = "admin-tok"
            mock_settings.reviewer_token = "reviewer-tok"
            with pytest.raises(HTTPException) as exc_info:
                self._run(require_auth(authorization="Bearer "))
            assert exc_info.value.status_code == 401

    def test_wrong_token_raises_401(self):
        """A token that does not match admin or reviewer token → 401."""
        from services.common.auth import require_auth
        from fastapi import HTTPException

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = "correct-admin"
            mock_settings.reviewer_token = "correct-reviewer"
            with pytest.raises(HTTPException) as exc_info:
                self._run(require_auth(authorization="Bearer totally-wrong-token"))
            assert exc_info.value.status_code == 401
            assert exc_info.value.headers.get("WWW-Authenticate") == "Bearer"

    def test_wrong_token_error_detail(self):
        """401 for wrong token includes 'denied' in the detail message."""
        from services.common.auth import require_auth
        from fastapi import HTTPException

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = "correct-admin"
            mock_settings.reviewer_token = "correct-reviewer"
            with pytest.raises(HTTPException) as exc_info:
                self._run(require_auth(authorization="Bearer wrong"))
            assert "denied" in exc_info.value.detail.lower() or "invalid" in exc_info.value.detail.lower()

    def test_bearer_case_insensitive_scheme(self):
        """'bearer' (lowercase) is accepted as the scheme."""
        from services.common.auth import require_auth

        with patch("services.common.config.settings") as mock_settings:
            mock_settings.admin_token = "my-admin-token"
            mock_settings.reviewer_token = ""
            result = self._run(require_auth(authorization="bearer my-admin-token"))
            assert result is None

    def test_token_comparison_is_constant_time(self):
        """Verify that token comparison uses compare_digest (timing-safe).

        This test is structural: it checks that the implementation calls
        hashlib.compare_digest inside require_auth by ensuring the import
        is present and the function does not use a naive == comparison.
        """
        import inspect
        from services.common import auth as auth_module

        source = inspect.getsource(auth_module.require_auth)
        assert "compare_digest" in source, (
            "require_auth must use hmac.compare_digest for constant-time comparison"
        )


# ---------------------------------------------------------------------------
# Integration tests via FastAPI TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_client():
    """TestClient with auth configured (admin + reviewer tokens set).

    Uses patch.object on the real settings instance so that only the token
    attributes are overridden.  All other settings retain their real values,
    which prevents type errors in middleware that reads integer settings.
    Uses an in-memory SQLite database.
    """
    from services.common.database import Database
    from services.common.config import settings as real_settings

    test_db = Database(":memory:")

    admin_token = "test-admin-secret"
    reviewer_token = "test-reviewer-secret"

    with patch("services.common.database.get_db", return_value=test_db), \
         patch("services.ingestion.router.get_db", return_value=test_db), \
         patch.object(real_settings, "admin_token", admin_token), \
         patch.object(real_settings, "reviewer_token", reviewer_token):

        from services.ingestion.main import app
        client = TestClient(app, raise_server_exceptions=False)
        client._admin_token = admin_token
        client._reviewer_token = reviewer_token
        yield client


@pytest.fixture
def noauth_client():
    """TestClient with NO auth configured (both tokens empty — bypass mode)."""
    from services.common.database import Database
    from services.common.config import settings as real_settings

    test_db = Database(":memory:")

    with patch("services.common.database.get_db", return_value=test_db), \
         patch("services.ingestion.router.get_db", return_value=test_db), \
         patch.object(real_settings, "admin_token", ""), \
         patch.object(real_settings, "reviewer_token", ""):

        from services.ingestion.main import app
        yield TestClient(app, raise_server_exceptions=False)


# -- Public endpoints: must always be accessible --------------------------


class TestPublicEndpoints:
    """Health check and read-only endpoints must remain public (no auth required)."""

    @pytest.mark.integration
    def test_health_check_v1_is_public(self, auth_client):
        """GET /api/v1/health returns 200 without any Authorization header."""
        response = auth_client.get("/api/v1/health")
        assert response.status_code == 200

    @pytest.mark.integration
    def test_health_check_legacy_is_public(self, auth_client):
        """GET /api/health returns 200 without any Authorization header."""
        response = auth_client.get("/api/health")
        assert response.status_code == 200

    @pytest.mark.integration
    def test_health_check_root_is_public(self, auth_client):
        """GET /health returns 200 without any Authorization header."""
        response = auth_client.get("/health")
        assert response.status_code == 200

    @pytest.mark.integration
    def test_list_documents_is_public(self, auth_client):
        """GET /api/v1/documents returns 200 without any Authorization header."""
        response = auth_client.get("/api/v1/documents")
        assert response.status_code == 200

    @pytest.mark.integration
    def test_get_document_is_public(self, auth_client):
        """GET /api/v1/documents/{id} returns 404 (not 401) for missing docs.

        This verifies the endpoint is accessible (not blocked by auth)
        even when the document does not exist.
        """
        response = auth_client.get("/api/v1/documents/nonexistent-doc-id")
        # 404 means the endpoint was reached (not blocked by 401)
        assert response.status_code == 404


# -- Protected endpoints: must return 401 when auth configured + no token --


class TestProtectedEndpointsReturn401:
    """POST endpoints that trigger paid APIs must return 401 when no token supplied."""

    @pytest.mark.integration
    def test_upload_requires_auth(self, auth_client):
        """POST /api/v1/documents/upload → 401 when no Authorization header."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = auth_client.post("/api/v1/documents/upload", files=files)
        assert response.status_code == 401

    @pytest.mark.integration
    def test_analyze_requires_auth(self, auth_client):
        """POST /api/v1/analyze → 401 when no Authorization header."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = auth_client.post("/api/v1/analyze", files=files)
        assert response.status_code == 401

    @pytest.mark.integration
    def test_remediate_requires_auth(self, auth_client):
        """POST /api/v1/remediate → 401 when no Authorization header."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = auth_client.post("/api/v1/remediate", files=files)
        assert response.status_code == 401

    @pytest.mark.integration
    def test_batch_approve_requires_auth(self, auth_client):
        """POST /api/v1/alt-text-proposals/batch-approve → 401 when no Authorization header."""
        payload = {"proposal_ids": ["pid-1"]}
        response = auth_client.post(
            "/api/v1/alt-text-proposals/batch-approve",
            json=payload,
        )
        assert response.status_code == 401

    @pytest.mark.integration
    def test_upload_returns_www_authenticate_header(self, auth_client):
        """401 response includes WWW-Authenticate: Bearer header."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = auth_client.post("/api/v1/documents/upload", files=files)
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    @pytest.mark.integration
    def test_upload_wrong_token_returns_401(self, auth_client):
        """POST /api/v1/documents/upload with wrong Bearer token → 401."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = auth_client.post(
            "/api/v1/documents/upload",
            files=files,
            headers={"Authorization": "Bearer completely-wrong-token"},
        )
        assert response.status_code == 401

    @pytest.mark.integration
    def test_analyze_wrong_token_returns_401(self, auth_client):
        """POST /api/v1/analyze with wrong Bearer token → 401."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = auth_client.post(
            "/api/v1/analyze",
            files=files,
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 401


# -- Protected endpoints: pass through when valid token supplied -----------


class TestProtectedEndpointsWithValidToken:
    """With a valid Bearer token, protected endpoints must NOT return 401.

    They may return other status codes (400, 422, 502 etc.) because the
    underlying handler logic runs — but they must not be blocked by auth.
    """

    @pytest.mark.integration
    def test_upload_with_admin_token_is_not_401(self, auth_client):
        """POST /api/v1/documents/upload with valid admin token passes auth gate."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = auth_client.post(
            "/api/v1/documents/upload",
            files=files,
            headers={"Authorization": f"Bearer {auth_client._admin_token}"},
        )
        # Auth passed: we get past the 401 gate.  The endpoint may still fail
        # for other reasons (missing GCS, bad PDF content, etc.) but NOT 401.
        assert response.status_code != 401, (
            f"Expected auth to pass, got 401. Detail: {response.text}"
        )

    @pytest.mark.integration
    def test_upload_with_reviewer_token_is_not_401(self, auth_client):
        """POST /api/v1/documents/upload with valid reviewer token passes auth gate."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = auth_client.post(
            "/api/v1/documents/upload",
            files=files,
            headers={"Authorization": f"Bearer {auth_client._reviewer_token}"},
        )
        assert response.status_code != 401, (
            f"Expected reviewer token to be accepted, got 401. Detail: {response.text}"
        )

    @pytest.mark.integration
    def test_analyze_with_valid_token_is_not_401(self, auth_client):
        """POST /api/v1/analyze with valid token passes auth gate."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = auth_client.post(
            "/api/v1/analyze",
            files=files,
            headers={"Authorization": f"Bearer {auth_client._admin_token}"},
        )
        assert response.status_code != 401

    @pytest.mark.integration
    def test_remediate_with_valid_token_is_not_401(self, auth_client):
        """POST /api/v1/remediate with valid token passes auth gate."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = auth_client.post(
            "/api/v1/remediate",
            files=files,
            headers={"Authorization": f"Bearer {auth_client._admin_token}"},
        )
        assert response.status_code != 401

    @pytest.mark.integration
    def test_batch_approve_with_valid_token_is_not_401(self, auth_client):
        """POST /api/v1/alt-text-proposals/batch-approve with valid token passes auth gate."""
        payload = {"proposal_ids": ["pid-1"]}
        response = auth_client.post(
            "/api/v1/alt-text-proposals/batch-approve",
            json=payload,
            headers={"Authorization": f"Bearer {auth_client._admin_token}"},
        )
        assert response.status_code != 401


# -- Bypass mode: no auth configured, everything is allowed ---------------


class TestBypassMode:
    """When both tokens are empty, all endpoints are accessible without a header."""

    @pytest.mark.integration
    def test_upload_accessible_in_bypass_mode(self, noauth_client):
        """POST /api/v1/documents/upload is accessible when no tokens configured."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = noauth_client.post("/api/v1/documents/upload", files=files)
        # Not 401 — auth gate is bypassed
        assert response.status_code != 401

    @pytest.mark.integration
    def test_analyze_accessible_in_bypass_mode(self, noauth_client):
        """POST /api/v1/analyze is accessible when no tokens configured."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = noauth_client.post("/api/v1/analyze", files=files)
        assert response.status_code != 401

    @pytest.mark.integration
    def test_remediate_accessible_in_bypass_mode(self, noauth_client):
        """POST /api/v1/remediate is accessible when no tokens configured."""
        files = {"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")}
        response = noauth_client.post("/api/v1/remediate", files=files)
        assert response.status_code != 401

    @pytest.mark.integration
    def test_batch_approve_accessible_in_bypass_mode(self, noauth_client):
        """POST /api/v1/alt-text-proposals/batch-approve is accessible in bypass mode."""
        payload = {"proposal_ids": ["pid-1"]}
        response = noauth_client.post(
            "/api/v1/alt-text-proposals/batch-approve",
            json=payload,
        )
        assert response.status_code != 401
