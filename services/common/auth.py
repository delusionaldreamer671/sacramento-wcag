"""Token-based authentication for the WCAG pipeline POC.

Two roles: admin (BridgeAI team), reviewer (county staff).
Tokens seeded from WCAG_ADMIN_TOKEN and WCAG_REVIEWER_TOKEN env vars
via the Settings object in services.common.config.

Token storage is in-memory for the POC. When the database layer
(services.common.database) is available, swap _USER_STORE lookups
for db.get_user_by_token() calls.

Usage in a FastAPI route:

    from services.common.auth import require_reviewer, require_admin

    @router.get("/items")
    def list_items(user: dict = Depends(require_reviewer)):
        ...

    @router.delete("/items/{id}")
    def delete_item(item_id: str, user: dict = Depends(require_admin)):
        ...
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bearer scheme — auto_error=False so we can return a custom 401 message
# ---------------------------------------------------------------------------

security = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# In-memory user store (POC).
# Keys are SHA-256 hashes of the raw bearer tokens.
# Values: {"token_hash": str, "user_id": str, "role": "admin" | "reviewer"}
# ---------------------------------------------------------------------------

_USER_STORE: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Token hashing
# ---------------------------------------------------------------------------


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a raw bearer token.

    Tokens are never stored in plain text. All comparisons use hashed values.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# User store helpers
# ---------------------------------------------------------------------------


def _get_user_by_token_hash(token_hash: str) -> Optional[dict]:
    """Look up a user by their hashed token. Returns None if not found."""
    return _USER_STORE.get(token_hash)


def _add_user(user_id: str, raw_token: str, role: str) -> None:
    """Insert a user into the in-memory store.

    Args:
        user_id:   Human-readable identifier (e.g. "admin", "reviewer").
        raw_token: Plain-text bearer token — hashed before storage.
        role:      "admin" or "reviewer".
    """
    if not raw_token:
        logger.warning("Skipping empty token for user_id=%s role=%s", user_id, role)
        return
    token_hash = hash_token(raw_token)
    _USER_STORE[token_hash] = {
        "token_hash": token_hash,
        "user_id": user_id,
        "role": role,
    }
    logger.info("Seeded user: user_id=%s role=%s", user_id, role)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> dict:
    """FastAPI dependency: validate Bearer token and return the user dict.

    Raises:
        HTTPException 401: if no token provided or token is unrecognised.

    Returns:
        User dict with keys: token_hash, user_id, role.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_hash = hash_token(credentials.credentials)
    user = _get_user_by_token_hash(token_hash)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """FastAPI dependency: require the authenticated user to have the admin role.

    Raises:
        HTTPException 403: if the user's role is not "admin".

    Returns:
        The authenticated user dict (same as get_current_user).
    """
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required for this operation.",
        )
    return user


async def require_reviewer(user: dict = Depends(get_current_user)) -> dict:
    """FastAPI dependency: require any authenticated user (admin or reviewer).

    Both roles are permitted. This is the baseline access guard — any user
    with a valid token is considered a reviewer.

    Raises:
        HTTPException 403: if the user has no recognised role.

    Returns:
        The authenticated user dict (same as get_current_user).
    """
    role = user.get("role", "")
    if role not in ("admin", "reviewer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reviewer or admin role required.",
        )
    return user


# ---------------------------------------------------------------------------
# Startup seeding
# ---------------------------------------------------------------------------


def seed_default_users() -> None:
    """Seed admin and reviewer accounts from environment variables.

    Reads WCAG_ADMIN_TOKEN and WCAG_REVIEWER_TOKEN from settings.
    Call this once at application startup (e.g. in the FastAPI on_startup hook).

    If tokens are empty strings the corresponding user is NOT created —
    the service starts without that role available. A warning is logged.

    Example (in FastAPI app startup):

        from services.common.auth import seed_default_users

        @app.on_event("startup")
        async def on_startup():
            seed_default_users()
    """
    from services.common.config import settings  # imported here to avoid circular deps

    if settings.admin_token:
        _add_user(user_id="admin", raw_token=settings.admin_token, role="admin")
    else:
        logger.warning(
            "WCAG_ADMIN_TOKEN is not set — admin role will not be available."
        )

    if settings.reviewer_token:
        _add_user(user_id="reviewer", raw_token=settings.reviewer_token, role="reviewer")
    else:
        logger.warning(
            "WCAG_REVIEWER_TOKEN is not set — reviewer role will not be available."
        )

    logger.info("Auth seed complete. Active users: %d", len(_USER_STORE))
