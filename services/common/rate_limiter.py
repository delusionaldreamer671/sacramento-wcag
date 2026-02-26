"""In-memory sliding window rate limiter middleware for FastAPI.

Provides two-tier rate limiting:
  - Upload/analyze/remediate endpoints: tighter limit (default 10 req/min per IP)
  - All other endpoints: standard limit (default 60 req/min per IP)

Designed for Cloud Run single-instance POC — no external dependencies (Redis, etc.).
Thread-safe via a single threading.Lock guarding the timestamp store.

Client IP is resolved from X-Forwarded-For (injected by the Cloud Run load balancer)
with a fallback to request.client.host for local development.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding window rate limiter implemented as Starlette middleware.

    Two bucket tiers per client IP:
    - "upload" bucket: applies to cost-intensive mutation paths defined in UPLOAD_PATHS.
    - "default" bucket: applies to every other path.

    Each bucket is a list of monotonic timestamps. On every request:
    1. Timestamps older than the 60-second window are evicted (O(n) but lists stay tiny).
    2. If the remaining count >= limit, return HTTP 429 with a Retry-After header.
    3. Otherwise append the current timestamp and let the request through.

    Memory bound: worst case is (default_rpm + upload_rpm) entries per active IP.
    At 60 req/min the list holds at most 60 floats (~480 bytes per IP) — negligible.
    """

    # Paths that receive the tighter upload rate limit.
    # Trailing-slash variants are intentionally excluded; FastAPI normalises paths.
    UPLOAD_PATHS: frozenset[str] = frozenset(
        {
            "/api/v1/upload",
            "/api/v1/analyze",
            "/api/v1/remediate",
            "/api/v1/batch-approve-alt-text",
        }
    )

    def __init__(
        self,
        app,
        default_rpm: int = 60,
        upload_rpm: int = 10,
    ) -> None:
        super().__init__(app)
        self.default_rpm = default_rpm
        self.upload_rpm = upload_rpm
        # Single lock guards the entire _requests dict — keeps the critical section
        # short (list slicing + append) so contention is negligible.
        self._lock = threading.Lock()
        # Mapping: bucket_key -> list of monotonic request timestamps (float).
        # bucket_key format: "<ip>:upload" or "<ip>:default"
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _get_client_ip(self, request: Request) -> str:
        """Extract the real client IP from request headers or connection info.

        Cloud Run's load balancer appends the originating IP as the first entry
        in X-Forwarded-For.  Multiple proxies produce comma-separated entries;
        we always use the leftmost (original client).

        Falls back to request.client.host for local uvicorn runs where no proxy
        header is present.
        """
        forwarded_for = request.headers.get("x-forwarded-for", "").strip()
        if forwarded_for:
            # Take the leftmost (client) entry; strip any port suffix.
            return forwarded_for.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    async def dispatch(self, request: Request, call_next) -> Response:
        client_ip = self._get_client_ip(request)
        path = request.url.path
        is_upload = path in self.UPLOAD_PATHS
        limit = self.upload_rpm if is_upload else self.default_rpm
        bucket_key = f"{client_ip}:{'upload' if is_upload else 'default'}"

        now = time.monotonic()
        window = 60.0  # seconds

        with self._lock:
            # Evict timestamps outside the sliding window.
            self._requests[bucket_key] = [
                t for t in self._requests[bucket_key] if now - t < window
            ]

            if len(self._requests[bucket_key]) >= limit:
                # Time until the oldest request in the window expires.
                oldest = self._requests[bucket_key][0]
                retry_after = int(window - (now - oldest)) + 1
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded",
                        "retry_after": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )

            self._requests[bucket_key].append(now)

        return await call_next(request)
