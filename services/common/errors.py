"""Structured error response model for the WCAG pipeline.

All API error responses include a machine-readable ``error_code``,
a human-readable ``detail`` message, and an optional ``trace_id``
for correlation with distributed traces.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PROCESSING_ERROR = "PROCESSING_ERROR"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMIT_ERROR = "RATE_LIMIT_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CAPACITY_ERROR = "CAPACITY_ERROR"


class ErrorResponse(BaseModel):
    error_code: str
    detail: str
    trace_id: str = ""


def get_current_trace_id() -> str:
    """Extract the current OpenTelemetry trace ID, or return empty string."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    except Exception:  # noqa: BLE001 — do not catch SystemExit/KeyboardInterrupt
        import logging
        logging.getLogger(__name__).debug(
            "get_current_trace_id: failed to extract trace ID", exc_info=True
        )
    return ""
