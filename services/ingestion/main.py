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

import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.models import PipelineHealthResponse
from services.ingestion.router import router
from services.ingestion.api_proposals import router as proposals_router
from services.ingestion.api_rules import router as rules_router
from services.ingestion.api_audit import router as audit_router
from services.ingestion.api_fixes import router as fixes_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

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
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS (HITL dashboard origin; tighten for production)
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to dashboard origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(router)
app.include_router(proposals_router)
app.include_router(rules_router)
app.include_router(audit_router)
app.include_router(fixes_router)

# ---------------------------------------------------------------------------
# Health check (at root level, outside /api prefix)
# ---------------------------------------------------------------------------


@app.get(
    "/api/health",
    response_model=PipelineHealthResponse,
    tags=["health"],
    summary="Service health check",
)
def health_check() -> PipelineHealthResponse:
    """Returns 200 when the service is running.

    Cloud Run uses this endpoint for startup and liveness probes.
    """
    logger.debug("Health check called")
    return PipelineHealthResponse(
        status="healthy",
        services={"ingestion": "up"},
    )


# ---------------------------------------------------------------------------
# Startup / shutdown events
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def on_startup() -> None:
    from services.common.auth import seed_default_users
    from services.common.telemetry import init_telemetry
    seed_default_users()
    init_telemetry(app)
    logger.info("Ingestion service starting up")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    from services.common.telemetry import shutdown as otel_shutdown
    otel_shutdown()
    logger.info("Ingestion service shutting down")
