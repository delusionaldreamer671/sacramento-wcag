"""Token-based authentication for the WCAG pipeline POC.

Two roles: admin (BridgeAI team), reviewer (county staff).
Tokens seeded from WCAG_ADMIN_TOKEN and WCAG_REVIEWER_TOKEN env vars
via the Settings object in services.common.config.

Hash strategy (Phase 2B upgrade):
  - New tokens are hashed with argon2id via argon2-cffi (if available).
    Argon2id is the current best-practice password hashing algorithm.
  - Legacy tokens hashed with SHA-256 are still accepted via dual-hash
    verify_token(), allowing zero-downtime migration.
  - hash_token() continues to return a SHA-256 hex digest for backward
    compatibility with existing tests and seeded records.
  - hash_algorithm field tracks which algorithm a stored hash uses.

Token storage:
  - Primary:  database via get_db().get_user_by_token() (when DB available)
  - Cache:    in-memory _USER_STORE dict with 5-minute TTL per entry
  - Fallback: _USER_STORE only (for tests and environments without a DB)

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
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional argon2 import — graceful degradation if package not installed
# ---------------------------------------------------------------------------

try:
    from argon2 import PasswordHasher as _PasswordHasher
    from argon2.exceptions import VerifyMismatchError as _VerifyMismatchError
    _ARGON2_AVAILABLE = True
    _ph = _PasswordHasher()
except ImportError:  # pragma: no cover
    _ARGON2_AVAILABLE = False
    _PasswordHasher = None  # type: ignore[assignment,misc]
    _VerifyMismatchError = None  # type: ignore[assignment,misc]
    _ph = None  # type: ignore[assignment]
    logger.warning(
        "argon2-cffi is not installed — token hashing will fall back to SHA-256. "
        "Install argon2-cffi>=23.1.0 for production-grade token security."
    )

# ---------------------------------------------------------------------------
# Bearer scheme — auto_error=False so we can return a custom 401 message
# ---------------------------------------------------------------------------

security = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# In-memory user store (cache + fallback).
# Keys are token hashes (SHA-256 hex for legacy, argon2id string for new).
# Values: {
#   "token_hash": str,
#   "user_id": str,
#   "role": "admin" | "reviewer",
#   "hash_algorithm": "sha256" | "argon2id",
#   "_raw_token": str,    # stored only for argon2 re-verification at verify time
#   "_cached_at": float,  # unix timestamp for TTL eviction
# }
# ---------------------------------------------------------------------------

_USER_STORE: dict[str, dict] = {}

# Cache TTL — entries older than this are re-validated against the DB
_CACHE_TTL_SECONDS: int = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Token hashing
# ---------------------------------------------------------------------------


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a raw bearer token.

    This function is preserved for backward compatibility: it is called by
    tests that expect a 64-character hex string.  New code should use
    hash_token_argon2() for storage and verify_token() for verification.

    Tokens are never stored in plain text.  All comparisons use hashed values.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_token_argon2(token: str) -> tuple[str, str]:
    """Return an argon2id hash of a raw bearer token.

    Returns:
        (token_hash, hash_algorithm) — use hash_algorithm to record which
        algorithm produced this hash so verify_token() can select the right path.

    Falls back to SHA-256 if argon2-cffi is not installed.
    """
    if _ARGON2_AVAILABLE and _ph is not None:
        return _ph.hash(token), "argon2id"
    # Graceful degradation
    return hash_token(token), "sha256"


def verify_token(raw_token: str, stored_hash: str, hash_algorithm: str) -> bool:
    """Verify a raw bearer token against a stored hash.

    Supports dual-hash verification:
      - argon2id: uses argon2.PasswordHasher.verify(); raises VerifyMismatchError on failure
      - sha256:   constant-time comparison of SHA-256 digests

    Args:
        raw_token:      The bearer token as received from the HTTP header.
        stored_hash:    The hash stored in the database or _USER_STORE.
        hash_algorithm: "argon2id" or "sha256".

    Returns:
        True if the token matches; False otherwise.
    """
    if hash_algorithm == "argon2id" and _ARGON2_AVAILABLE and _ph is not None:
        try:
            return _ph.verify(stored_hash, raw_token)
        except _VerifyMismatchError:
            return False
        except Exception as exc:  # pragma: no cover
            logger.warning("argon2 verify raised unexpected error: %s", exc)
            return False
    # SHA-256 path (legacy tokens or fallback)
    return hashlib.compare_digest(hash_token(raw_token), stored_hash)


# ---------------------------------------------------------------------------
# User store helpers
# ---------------------------------------------------------------------------


def _is_cache_entry_fresh(entry: dict) -> bool:
    """Return True if the cache entry is within the TTL window."""
    cached_at = entry.get("_cached_at", 0.0)
    return (time.time() - cached_at) < _CACHE_TTL_SECONDS


def _get_user_by_token_hash(token_hash: str) -> Optional[dict]:
    """Look up a user by their SHA-256 token hash from the in-memory store.

    This function is intentionally limited to the in-memory store so that
    existing tests (which seed _USER_STORE directly) continue to work.
    """
    entry = _USER_STORE.get(token_hash)
    if entry is None:
        return None
    if not _is_cache_entry_fresh(entry):
        del _USER_STORE[token_hash]
        return None
    return entry


def _lookup_user_by_raw_token(raw_token: str) -> Optional[dict]:
    """Full lookup: in-memory cache first, then database.

    Performs dual-hash verification (argon2id or sha256) for users found in
    the database, and refreshes the cache on a hit.

    Returns:
        User dict with keys: token_hash, user_id, role, hash_algorithm.
        Returns None if the token does not match any active user.
    """
    # 1. Try in-memory store (SHA-256 keyed, for legacy seeded entries)
    sha_hash = hash_token(raw_token)
    cached = _USER_STORE.get(sha_hash)
    if cached is not None and _is_cache_entry_fresh(cached):
        # Verify the raw token against the stored hash (dual-path)
        alg = cached.get("hash_algorithm", "sha256")
        stored = cached.get("token_hash", sha_hash)
        if verify_token(raw_token, stored, alg):
            return cached

    # 2. Try database
    try:
        from services.common.database import get_db
        from services.common.config import settings
        db = get_db(settings.db_path)
        # SHA-256 lookup (legacy records stored with sha256 hash)
        db_user = db.get_user_by_token(sha_hash)
        if db_user is not None:
            alg = db_user.get("hash_algorithm", "sha256")
            if verify_token(raw_token, db_user["token_hash"], alg):
                # Check token expiry before accepting the user
                expires_at = db_user.get("token_expires_at")
                if expires_at:
                    try:
                        expiry_dt = datetime.fromisoformat(expires_at)
                        if expiry_dt.tzinfo is None:
                            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
                        if datetime.now(timezone.utc) > expiry_dt:
                            logger.warning(
                                "Rejected expired token for user=%s (expired=%s)",
                                db_user.get("username", "unknown"),
                                expires_at,
                            )
                            return None
                    except (ValueError, TypeError) as exc:
                        logger.warning(
                            "Could not parse token_expires_at=%r for user=%s: %s",
                            expires_at,
                            db_user.get("username", "unknown"),
                            exc,
                        )
                # Refresh cache
                entry = {
                    "token_hash": db_user["token_hash"],
                    "user_id": db_user.get("username", db_user.get("user_id", "")),
                    "role": db_user["role"],
                    "hash_algorithm": alg,
                    "_cached_at": time.time(),
                }
                _USER_STORE[sha_hash] = entry
                return entry
            else:
                logger.warning(
                    "Token verification failed for DB user username=%s",
                    db_user.get("username", "unknown"),
                )
    except Exception as exc:
        logger.warning("DB lookup failed during token verification: %s", exc)

    # 3. Final fallback: re-check in-memory store (handles argon2id seeded users)
    #    Iterate only entries that have _raw_token stored (argon2 entries)
    for _key, entry in list(_USER_STORE.items()):
        if not _is_cache_entry_fresh(entry):
            continue
        alg = entry.get("hash_algorithm", "sha256")
        if alg == "argon2id":
            stored = entry.get("token_hash", "")
            if stored and verify_token(raw_token, stored, alg):
                return entry

    return None


def _add_user(user_id: str, raw_token: str, role: str) -> None:
    """Insert a user into the in-memory store using SHA-256 hashing.

    Preserved for backward compatibility with existing tests and seed calls
    that use SHA-256 directly.  New startup code should use _add_user_argon2().

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
        "hash_algorithm": "sha256",
        "_cached_at": time.time(),
    }
    logger.info("Seeded user (sha256): user_id=%s role=%s", user_id, role)


def _add_user_argon2(user_id: str, raw_token: str, role: str) -> None:
    """Insert a user into the in-memory store using argon2id hashing.

    Falls back to SHA-256 if argon2-cffi is not available.

    Args:
        user_id:   Human-readable identifier (e.g. "admin", "reviewer").
        raw_token: Plain-text bearer token — hashed before storage.
        role:      "admin" or "reviewer".
    """
    if not raw_token:
        logger.warning("Skipping empty token for user_id=%s role=%s", user_id, role)
        return
    token_hash, alg = hash_token_argon2(raw_token)
    # Use SHA-256 hash as the cache key so _get_user_by_token_hash (used by tests) works
    sha_hash = hash_token(raw_token)
    _USER_STORE[sha_hash] = {
        "token_hash": token_hash,
        "user_id": user_id,
        "role": role,
        "hash_algorithm": alg,
        "_cached_at": time.time(),
    }
    logger.info("Seeded user (%s): user_id=%s role=%s", alg, user_id, role)


# ---------------------------------------------------------------------------
# Lightweight pipeline endpoint dependency
# ---------------------------------------------------------------------------


async def require_auth(authorization: Optional[str] = Header(None)) -> None:
    """FastAPI dependency: protect pipeline endpoints that trigger paid external APIs.

    Validates the Bearer token from the Authorization header against the tokens
    configured in settings (admin_token and reviewer_token).

    Bypass rule:
        If BOTH settings.admin_token and settings.reviewer_token are empty strings
        (i.e. no auth is configured), ALL requests are allowed through.  This
        preserves backward compatibility for local development without a .env file.

    Args:
        authorization: Value of the ``Authorization`` HTTP header, injected by FastAPI.
                       Expected format: ``Bearer <token>``.

    Raises:
        HTTPException 401: when auth IS configured and the token is missing,
                           malformed, or does not match any valid token.

    Usage::

        from fastapi import Depends
        from services.common.auth import require_auth

        @router.post("/expensive-endpoint", dependencies=[Depends(require_auth)])
        async def expensive_endpoint(): ...
    """
    from services.common.config import settings  # imported here to avoid circular deps

    admin_tok = settings.admin_token.get_secret_value()
    reviewer_tok = settings.reviewer_token.get_secret_value()

    # Bypass: no tokens configured — allow all (local dev mode)
    if not admin_tok and not reviewer_tok:
        return

    # Auth IS configured — a valid Bearer token is required
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Authentication required. "
                "Provide a Bearer token via the Authorization header: "
                "Authorization: Bearer <token>"
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Parse the Bearer token out of the header value
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Invalid Authorization header format. "
                "Expected: Authorization: Bearer <token>"
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_token = token.strip()

    # Constant-time comparison against each configured token.
    # Hash both sides with SHA-256 before compare_digest so that the digests
    # are always the same length, which is required by hmac.compare_digest.
    raw_digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    admin_match = bool(admin_tok) and hmac.compare_digest(
        raw_digest,
        hashlib.sha256(admin_tok.encode("utf-8")).hexdigest(),
    )
    reviewer_match = bool(reviewer_tok) and hmac.compare_digest(
        raw_digest,
        hashlib.sha256(reviewer_tok.encode("utf-8")).hexdigest(),
    )

    if not admin_match and not reviewer_match:
        logger.warning(
            "require_auth: rejected invalid token (prefix=%s...)",
            raw_token[:4] if len(raw_token) >= 4 else "****",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Access denied.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.debug("require_auth: accepted token (admin=%s reviewer=%s)", admin_match, reviewer_match)


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

    user = _lookup_user_by_raw_token(credentials.credentials)

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
    Writes argon2id hashes to the database and caches them in _USER_STORE.
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

    admin_raw = settings.admin_token.get_secret_value()
    reviewer_raw = settings.reviewer_token.get_secret_value()

    if admin_raw:
        _seed_user_to_db_and_cache(
            user_id="admin",
            raw_token=admin_raw,
            role="admin",
        )
    else:
        logger.warning(
            "WCAG_ADMIN_TOKEN is not set — admin role will not be available."
        )

    if reviewer_raw:
        _seed_user_to_db_and_cache(
            user_id="reviewer",
            raw_token=reviewer_raw,
            role="reviewer",
        )
    else:
        logger.warning(
            "WCAG_REVIEWER_TOKEN is not set — reviewer role will not be available."
        )

    logger.info("Auth seed complete. Active users: %d", len(_USER_STORE))


def _seed_user_to_db_and_cache(user_id: str, raw_token: str, role: str) -> None:
    """Hash the token with argon2id, persist to DB, and cache in _USER_STORE."""
    token_hash, alg = hash_token_argon2(raw_token)
    sha_hash = hash_token(raw_token)

    # Persist to database (upsert by username so restarts don't duplicate rows)
    try:
        from services.common.database import get_db
        from services.common.config import settings
        db = get_db(settings.db_path)
        db.upsert_user(
            username=user_id,
            display_name=user_id.capitalize(),
            role=role,
            token_hash=token_hash,
            hash_algorithm=alg,
        )
        logger.info("Persisted user to DB: user_id=%s algorithm=%s", user_id, alg)
    except Exception as exc:
        logger.error(
            "CRITICAL: Could not persist user %s to DB: %s — "
            "refusing to fall back to in-memory (data would be lost on restart)",
            user_id, exc,
        )
        raise RuntimeError(
            f"User persistence to DB failed for {user_id}: {exc}. "
            "Fix the database connection before starting the service."
        ) from exc

    # Cache in _USER_STORE (keyed by sha256 for fast lookup path)
    _USER_STORE[sha_hash] = {
        "token_hash": token_hash,
        "user_id": user_id,
        "role": role,
        "hash_algorithm": alg,
        "_cached_at": time.time(),
    }
    logger.info("Seeded user (%s): user_id=%s role=%s", alg, user_id, role)
