"""Tests for the token-based authentication module."""

from __future__ import annotations

import pytest

from services.common.auth import (
    _USER_STORE,
    _add_user,
    _get_user_by_token_hash,
    get_current_user,
    hash_token,
    require_admin,
    require_reviewer,
)


# ---------------------------------------------------------------------------
# Setup/teardown
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_user_store():
    """Clear the in-memory user store before and after each test."""
    _USER_STORE.clear()
    yield
    _USER_STORE.clear()


# ---------------------------------------------------------------------------
# Token hashing
# ---------------------------------------------------------------------------


class TestTokenHashing:
    def test_hash_produces_hex_string(self):
        result = hash_token("my-secret-token")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest

    def test_same_token_same_hash(self):
        h1 = hash_token("token-abc")
        h2 = hash_token("token-abc")
        assert h1 == h2

    def test_different_tokens_different_hashes(self):
        h1 = hash_token("token-1")
        h2 = hash_token("token-2")
        assert h1 != h2

    def test_empty_token_hashes(self):
        result = hash_token("")
        assert isinstance(result, str)
        assert len(result) == 64


# ---------------------------------------------------------------------------
# User store
# ---------------------------------------------------------------------------


class TestUserStore:
    def test_add_user_stores_hashed_token(self):
        _add_user("admin", "raw-token-123", "admin")
        token_hash = hash_token("raw-token-123")
        user = _get_user_by_token_hash(token_hash)
        assert user is not None
        assert user["user_id"] == "admin"
        assert user["role"] == "admin"

    def test_add_user_with_empty_token_skips(self):
        _add_user("admin", "", "admin")
        assert len(_USER_STORE) == 0

    def test_lookup_nonexistent_token(self):
        result = _get_user_by_token_hash("nonexistent-hash")
        assert result is None

    def test_add_multiple_users(self):
        _add_user("admin", "admin-token", "admin")
        _add_user("reviewer", "reviewer-token", "reviewer")
        assert len(_USER_STORE) == 2

        admin = _get_user_by_token_hash(hash_token("admin-token"))
        reviewer = _get_user_by_token_hash(hash_token("reviewer-token"))

        assert admin["role"] == "admin"
        assert reviewer["role"] == "reviewer"


# ---------------------------------------------------------------------------
# FastAPI dependencies (unit tests — no HTTP server)
# ---------------------------------------------------------------------------


class TestDependencies:
    def test_get_current_user_no_credentials_raises_401(self):
        import asyncio
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(get_current_user(None))
        assert exc_info.value.status_code == 401

    def test_require_admin_rejects_reviewer(self):
        import asyncio
        from fastapi import HTTPException

        user = {"user_id": "reviewer-1", "role": "reviewer", "token_hash": "abc"}
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(require_admin(user))
        assert exc_info.value.status_code == 403

    def test_require_admin_accepts_admin(self):
        import asyncio

        user = {"user_id": "admin-1", "role": "admin", "token_hash": "abc"}
        result = asyncio.get_event_loop().run_until_complete(require_admin(user))
        assert result["role"] == "admin"

    def test_require_reviewer_accepts_reviewer(self):
        import asyncio

        user = {"user_id": "rev-1", "role": "reviewer", "token_hash": "abc"}
        result = asyncio.get_event_loop().run_until_complete(require_reviewer(user))
        assert result["role"] == "reviewer"

    def test_require_reviewer_accepts_admin(self):
        import asyncio

        user = {"user_id": "admin-1", "role": "admin", "token_hash": "abc"}
        result = asyncio.get_event_loop().run_until_complete(require_reviewer(user))
        assert result["role"] == "admin"

    def test_require_reviewer_rejects_unknown_role(self):
        import asyncio
        from fastapi import HTTPException

        user = {"user_id": "guest", "role": "guest", "token_hash": "abc"}
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(require_reviewer(user))
        assert exc_info.value.status_code == 403
