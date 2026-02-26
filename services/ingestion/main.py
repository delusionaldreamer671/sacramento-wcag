"""Ingestion service entry point.

Starts the FastAPI application that accepts PDF uploads, stores them
in GCS, and queues documents for the extraction pipeline via Pub/Sub.

Run locally:
    uvicorn services.ingestion.main:app --port 8000 --reload

Cloud Run:
    The container CMD is set to uvicorn with --host 0.0.0.0 --port $PORT.
"""

from __future__ import annotations

# Load ALL .env vars (including GOOGLE_APPLICATION_CREDENTIALS) before any
# other imports so GCP/Adobe SDKs pick them up.
from dotenv import load_dotenv
load_dotenv(override=True)

import asyncio
import json as _json_mod
import logging
import os
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from services.common.config import settings
from services.common.constants import API_V1_PREFIX
from services.common.rate_limiter import RateLimitMiddleware
from services.common.models import PipelineHealthResponse
from services.ingestion.router import router
from services.ingestion.api_proposals import router as proposals_router
from services.ingestion.api_rules import router as rules_router
from services.ingestion.api_audit import router as audit_router
from services.ingestion.api_fixes import router as fixes_router
from services.ingestion.api_review_items import router as review_items_router

# ---------------------------------------------------------------------------
# Logging — JSON on Cloud Run, human-readable locally
# ---------------------------------------------------------------------------

_ON_CLOUD_RUN = bool(os.getenv("K_SERVICE"))


class _JsonLogFormatter(logging.Formatter):
    """Structured JSON log formatter for Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "timestamp": self.formatTime(record, self.datefmt),
        }
        # OTel log bridge injects these attributes when available
        trace_id = getattr(record, "otelTraceID", "")
        span_id = getattr(record, "otelSpanID", "")
        if trace_id and trace_id != "0":
            payload["logging.googleapis.com/trace"] = trace_id
            payload["otelTraceID"] = trace_id
        if span_id and span_id != "0":
            payload["otelSpanID"] = span_id
        if record.exc_info and record.exc_info[1]:
            payload["exception"] = self.formatException(record.exc_info)
        return _json_mod.dumps(payload, default=str)


if _ON_CLOUD_RUN:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(_JsonLogFormatter())
    logging.root.handlers.clear()
    logging.root.addHandler(_handler)
    logging.root.setLevel(logging.INFO)
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Sacramento WCAG — Ingestion Service",
    description=(
        "Receives PDF documents from county staff, uploads them to GCS, "
        "and queues them for the WCAG remediation pipeline."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS (HITL dashboard origin; tighten for production)
# ---------------------------------------------------------------------------

_cors_origins = [
    origin.strip()
    for origin in settings.cors_allowed_origins.split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Task-Id", "X-Pipeline-Version", "X-Pipeline-Metadata", "X-Remediation-Delta"],
)

# Rate limiting — registered after CORS so it executes before CORS in the
# middleware chain (Starlette applies middleware in reverse-registration order).
# Limits are read from settings so they can be tuned via env vars without a
# code change.
app.add_middleware(
    RateLimitMiddleware,
    default_rpm=settings.rate_limit_per_minute,
    upload_rpm=settings.rate_limit_upload_per_minute,
)


class VersionHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Pipeline-Version"] = app.version
        return response


app.add_middleware(VersionHeaderMiddleware)


class APIVersionRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect legacy /api/* paths to /api/v1/* with HTTP 308.

    308 Permanent Redirect preserves the original HTTP method (unlike 301,
    which some clients downgrade to GET). This keeps POST/PATCH/DELETE
    requests intact during the /api/ -> /api/v1/ transition.

    Excluded from redirect:
      - /api/health  — kept as a direct alias for backward compatibility
      - /api/v1/*    — already versioned, no redirect needed
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Already versioned or is the health alias — pass through
        if path.startswith("/api/v1/") or path == "/api/health":
            return await call_next(request)
        # Legacy /api/* path — redirect to /api/v1/*
        if path.startswith("/api/"):
            suffix = path[len("/api"):]  # includes leading slash
            new_path = f"/api/v1{suffix}"
            # Preserve query string
            qs = request.url.query
            redirect_url = new_path if not qs else f"{new_path}?{qs}"
            return RedirectResponse(url=redirect_url, status_code=308)
        return await call_next(request)


app.add_middleware(APIVersionRedirectMiddleware)

# ---------------------------------------------------------------------------
# Prometheus metrics — conditional on package availability
# ---------------------------------------------------------------------------

try:
    from prometheus_fastapi_instrumentator import Instrumentator
    _instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_group_untemplated=True,
        excluded_handlers=["/health", "/docs", "/redoc", "/openapi.json"],
    )
    _instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
except ImportError:
    pass  # prometheus not installed, skip metrics


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from services.common.errors import ErrorCode, get_current_trace_id

    logger.exception("Unhandled error: %s", exc)
    trace_id = get_current_trace_id()
    return JSONResponse(
        status_code=500,
        content={
            "error_code": ErrorCode.PROCESSING_ERROR,
            "detail": "Internal server error",
            "trace_id": trace_id,
        },
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(router)
app.include_router(proposals_router)
app.include_router(rules_router)
app.include_router(audit_router)
app.include_router(fixes_router)
app.include_router(review_items_router)

# ---------------------------------------------------------------------------
# Health check (at root level, outside /api prefix)
# ---------------------------------------------------------------------------


@app.get(
    f"{API_V1_PREFIX}/health",
    response_model=PipelineHealthResponse,
    tags=["health"],
    summary="Service health check (v1)",
)
@app.get(
    "/api/health",
    response_model=PipelineHealthResponse,
    tags=["health"],
    summary="Service health check (legacy alias)",
    include_in_schema=False,
)
@app.get(
    "/health",
    response_model=PipelineHealthResponse,
    tags=["health"],
    summary="Service health check (root alias)",
    include_in_schema=False,
)
def health_check() -> PipelineHealthResponse:
    """Returns 200 with real dependency status.

    Cloud Run uses this endpoint for startup and liveness probes.
    ``/api/v1/health``, ``/api/health``, and ``/health`` are all supported.
    """
    from services.common.dependency_probes import run_all_probes, probes_to_health_response

    results = run_all_probes()
    health = probes_to_health_response(results)

    return PipelineHealthResponse(status=health["status"], services=health["services"])


@app.get(
    f"{API_V1_PREFIX}/images/{{image_id}}",
    tags=["assets"],
    summary="Serve extracted image for HITL preview (v1)",
)
@app.get(
    "/api/images/{image_id}",
    tags=["assets"],
    summary="Serve extracted image for HITL preview (legacy alias)",
    include_in_schema=False,
)
def serve_image(image_id: str) -> Response:
    """Return image bytes stored during PDF extraction."""
    from services.common.database import get_db

    db = get_db(settings.db_path)
    row = db.get_image_asset(image_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return Response(
        content=row["image_data"],
        media_type=row["mime_type"],
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------------------------------------------------------------------------
# Startup / shutdown events
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Concurrency semaphore — limits concurrent document processing
# ---------------------------------------------------------------------------

_pipeline_semaphore: asyncio.Semaphore | None = None


def get_pipeline_semaphore() -> asyncio.Semaphore:
    """Return the pipeline concurrency semaphore, creating it lazily."""
    global _pipeline_semaphore
    if _pipeline_semaphore is None:
        from services.common.config import settings
        _pipeline_semaphore = asyncio.Semaphore(settings.max_concurrent_documents)
    return _pipeline_semaphore


@app.on_event("startup")
async def on_startup() -> None:
    from services.common.auth import seed_default_users
    from services.common.telemetry import init_telemetry
    seed_default_users()
    init_telemetry(app)
    # Initialize the semaphore at startup (must be in an async context)
    get_pipeline_semaphore()
    # Run dependency probes at startup and log results
    from services.common.dependency_probes import run_all_probes
    probe_results = run_all_probes()
    for r in probe_results:
        level = logging.ERROR if r.status == "down" and r.required else (
            logging.WARNING if r.status == "down" else logging.INFO
        )
        logger.log(
            level,
            "Dependency probe [%s]: status=%s latency=%.1fms%s",
            r.name, r.status, r.latency_ms,
            f" ({r.message})" if r.message else "",
        )
    logger.info("Ingestion service starting up")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    from services.common.telemetry import shutdown as otel_shutdown
    otel_shutdown()
    logger.info("Ingestion service shutting down")
