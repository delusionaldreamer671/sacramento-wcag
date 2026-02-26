"""Tests for the in-memory sliding window rate limiter middleware.

Covers:
- IP extraction from X-Forwarded-For and fallback to request.client.host
- Default (60 req/min) and upload (10 req/min) bucket tiers
- HTTP 429 response shape: status code, body, Retry-After header
- Sliding window expiry: requests older than 60 s no longer count
- Thread safety: concurrent requests from multiple threads
- Independent bucket isolation: upload limit does not bleed into default limit
"""

from __future__ import annotations

import threading
import time
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.common.rate_limiter import RateLimitMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    path: str = "/api/v1/health",
    forwarded_for: str | None = None,
    client_host: str = "127.0.0.1",
) -> MagicMock:
    """Build a minimal mock Starlette Request."""
    request = MagicMock()
    request.url.path = path
    request.client = MagicMock()
    request.client.host = client_host

    headers: dict[str, str] = {}
    if forwarded_for is not None:
        headers["x-forwarded-for"] = forwarded_for
    request.headers = headers
    return request


async def _ok_response(*args: Any, **kwargs: Any) -> MagicMock:
    """Stub call_next that returns a 200 response."""
    response = MagicMock()
    response.status_code = 200
    return response


def _make_middleware(default_rpm: int = 60, upload_rpm: int = 10) -> RateLimitMiddleware:
    """Instantiate the middleware with a no-op app stub."""
    app_stub = MagicMock()
    # Bypass BaseHTTPMiddleware.__init__ complexity while keeping our __init__ logic.
    middleware = RateLimitMiddleware.__new__(RateLimitMiddleware)
    RateLimitMiddleware.__init__(middleware, app_stub, default_rpm=default_rpm, upload_rpm=upload_rpm)
    return middleware


# ---------------------------------------------------------------------------
# IP extraction
# ---------------------------------------------------------------------------


class TestClientIPExtraction:
    def test_uses_first_entry_of_forwarded_for(self):
        middleware = _make_middleware()
        request = _make_request(forwarded_for="10.0.0.1, 10.0.0.2, 10.0.0.3")
        ip = middleware._get_client_ip(request)
        assert ip == "10.0.0.1"

    def test_single_entry_forwarded_for(self):
        middleware = _make_middleware()
        request = _make_request(forwarded_for="203.0.113.5")
        ip = middleware._get_client_ip(request)
        assert ip == "203.0.113.5"

    def test_strips_whitespace_from_forwarded_for(self):
        middleware = _make_middleware()
        request = _make_request(forwarded_for="  192.168.1.1 , 10.0.0.1")
        ip = middleware._get_client_ip(request)
        assert ip == "192.168.1.1"

    def test_falls_back_to_client_host_when_no_header(self):
        middleware = _make_middleware()
        request = _make_request(forwarded_for=None, client_host="172.16.0.5")
        ip = middleware._get_client_ip(request)
        assert ip == "172.16.0.5"

    def test_unknown_when_no_forwarded_for_and_no_client(self):
        middleware = _make_middleware()
        request = _make_request(forwarded_for=None)
        request.client = None
        ip = middleware._get_client_ip(request)
        assert ip == "unknown"


# ---------------------------------------------------------------------------
# Default bucket (general endpoints)
# ---------------------------------------------------------------------------


class TestDefaultBucket:
    @pytest.mark.asyncio
    async def test_allows_requests_under_limit(self):
        middleware = _make_middleware(default_rpm=5, upload_rpm=2)
        request = _make_request(path="/api/v1/health", forwarded_for="1.2.3.4")

        for _ in range(5):
            response = await middleware.dispatch(request, _ok_response)
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_blocks_request_at_limit(self):
        middleware = _make_middleware(default_rpm=3, upload_rpm=1)
        request = _make_request(path="/api/v1/health", forwarded_for="1.2.3.4")

        # Exhaust the limit.
        for _ in range(3):
            response = await middleware.dispatch(request, _ok_response)
            assert response.status_code == 200

        # Next request must be rejected.
        response = await middleware.dispatch(request, _ok_response)
        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_429_body_contains_detail_and_retry_after(self):
        middleware = _make_middleware(default_rpm=1, upload_rpm=1)
        request = _make_request(path="/api/v1/health", forwarded_for="1.2.3.4")

        await middleware.dispatch(request, _ok_response)
        response = await middleware.dispatch(request, _ok_response)

        assert response.status_code == 429
        body = response.body  # JSONResponse stores body as bytes
        import json
        payload = json.loads(body)
        assert payload["detail"] == "Rate limit exceeded"
        assert "retry_after" in payload
        assert isinstance(payload["retry_after"], int)
        assert payload["retry_after"] >= 1

    @pytest.mark.asyncio
    async def test_retry_after_header_present_on_429(self):
        middleware = _make_middleware(default_rpm=1, upload_rpm=1)
        request = _make_request(path="/api/v1/health", forwarded_for="5.6.7.8")

        await middleware.dispatch(request, _ok_response)
        response = await middleware.dispatch(request, _ok_response)

        assert response.status_code == 429
        assert "Retry-After" in response.headers
        retry_after = int(response.headers["Retry-After"])
        assert 1 <= retry_after <= 61  # within a 60-second window


# ---------------------------------------------------------------------------
# Upload bucket (cost-intensive endpoints)
# ---------------------------------------------------------------------------


class TestUploadBucket:
    @pytest.mark.asyncio
    async def test_upload_path_uses_upload_limit(self):
        # upload_rpm=2 but default_rpm=100 — upload should be blocked at 2, not 100.
        middleware = _make_middleware(default_rpm=100, upload_rpm=2)
        request = _make_request(path="/api/v1/upload", forwarded_for="9.8.7.6")

        for _ in range(2):
            response = await middleware.dispatch(request, _ok_response)
            assert response.status_code == 200

        # Third request must be rejected.
        response = await middleware.dispatch(request, _ok_response)
        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_analyze_path_uses_upload_limit(self):
        middleware = _make_middleware(default_rpm=100, upload_rpm=1)
        request = _make_request(path="/api/v1/analyze", forwarded_for="2.2.2.2")

        await middleware.dispatch(request, _ok_response)
        response = await middleware.dispatch(request, _ok_response)
        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_remediate_path_uses_upload_limit(self):
        middleware = _make_middleware(default_rpm=100, upload_rpm=1)
        request = _make_request(path="/api/v1/remediate", forwarded_for="3.3.3.3")

        await middleware.dispatch(request, _ok_response)
        response = await middleware.dispatch(request, _ok_response)
        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_batch_approve_alt_text_uses_upload_limit(self):
        middleware = _make_middleware(default_rpm=100, upload_rpm=1)
        request = _make_request(
            path="/api/v1/batch-approve-alt-text", forwarded_for="4.4.4.4"
        )

        await middleware.dispatch(request, _ok_response)
        response = await middleware.dispatch(request, _ok_response)
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# Bucket isolation — upload and default limits are independent per IP
# ---------------------------------------------------------------------------


class TestBucketIsolation:
    @pytest.mark.asyncio
    async def test_upload_limit_does_not_consume_default_quota(self):
        """Exhausting the upload bucket must not block the default bucket for the same IP."""
        middleware = _make_middleware(default_rpm=5, upload_rpm=1)
        ip = "7.7.7.7"

        upload_request = _make_request(path="/api/v1/upload", forwarded_for=ip)
        default_request = _make_request(path="/api/v1/health", forwarded_for=ip)

        # Exhaust upload bucket.
        await middleware.dispatch(upload_request, _ok_response)
        upload_blocked = await middleware.dispatch(upload_request, _ok_response)
        assert upload_blocked.status_code == 429

        # Default bucket for the same IP must still be open.
        default_ok = await middleware.dispatch(default_request, _ok_response)
        assert default_ok.status_code == 200

    @pytest.mark.asyncio
    async def test_different_ips_have_independent_buckets(self):
        middleware = _make_middleware(default_rpm=1, upload_rpm=1)

        request_a = _make_request(path="/api/v1/health", forwarded_for="10.0.0.1")
        request_b = _make_request(path="/api/v1/health", forwarded_for="10.0.0.2")

        # IP A exhausts its quota.
        await middleware.dispatch(request_a, _ok_response)
        blocked_a = await middleware.dispatch(request_a, _ok_response)
        assert blocked_a.status_code == 429

        # IP B is a completely separate bucket — must still pass.
        ok_b = await middleware.dispatch(request_b, _ok_response)
        assert ok_b.status_code == 200


# ---------------------------------------------------------------------------
# Sliding window expiry
# ---------------------------------------------------------------------------


class TestSlidingWindow:
    @pytest.mark.asyncio
    async def test_expired_timestamps_no_longer_count(self):
        """After the window expires, the bucket resets and requests are allowed again."""
        middleware = _make_middleware(default_rpm=2, upload_rpm=1)
        request = _make_request(path="/api/v1/health", forwarded_for="11.22.33.44")

        # Inject a timestamp that is 70 seconds old (outside the 60-second window).
        with middleware._lock:
            middleware._requests["11.22.33.44:default"] = [time.monotonic() - 70]

        # The stale entry should be evicted; two new requests must succeed.
        for _ in range(2):
            response = await middleware.dispatch(request, _ok_response)
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_timestamps_at_window_boundary_still_count(self):
        """A timestamp 59.9 s old is still inside the 60-second window."""
        middleware = _make_middleware(default_rpm=1, upload_rpm=1)
        request = _make_request(path="/api/v1/health", forwarded_for="55.66.77.88")

        # Inject a timestamp that is 59.9 seconds old — still inside the window.
        with middleware._lock:
            middleware._requests["55.66.77.88:default"] = [time.monotonic() - 59.9]

        # Limit is 1; the old entry still occupies the slot → 429.
        response = await middleware.dispatch(request, _ok_response)
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_requests_do_not_exceed_limit(self):
        """Multiple threads hammering the same IP must not let more than `limit` through."""
        limit = 5
        middleware = _make_middleware(default_rpm=limit, upload_rpm=limit)
        request = _make_request(path="/api/v1/health", forwarded_for="99.99.99.99")

        import asyncio

        results: list[int] = []
        lock = threading.Lock()

        def send_request() -> None:
            # Each thread gets its own event loop to run the coroutine.
            loop = asyncio.new_event_loop()
            try:
                response = loop.run_until_complete(
                    middleware.dispatch(request, _ok_response)
                )
                with lock:
                    results.append(response.status_code)
            finally:
                loop.close()

        threads = [threading.Thread(target=send_request) for _ in range(limit * 3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed = results.count(200)
        blocked = results.count(429)

        assert allowed == limit, f"Expected exactly {limit} allowed, got {allowed}"
        assert blocked == limit * 2, f"Expected {limit * 2} blocked, got {blocked}"
        assert allowed + blocked == len(results)
