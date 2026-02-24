"""Extraction service entry point.

Receives Pub/Sub push messages from the ingestion service, runs the
Adobe Extract + Auto-Tag pipeline on each PDF, parses WCAGFinding
objects, and publishes results to the AI-drafting topic.

Pub/Sub push delivery model (Cloud Run):
    Cloud Run receives HTTP POST at /api/extract containing the
    Pub/Sub message envelope. On success, returns HTTP 200/204 to
    acknowledge the message. On failure, returns 4xx/5xx so Pub/Sub
    can retry according to the subscription's retry policy.

Run locally:
    uvicorn services.extraction.main:app --port 8001 --reload
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

from services.common import gcs_client, pubsub_client
from services.common.config import settings
from services.common.models import (
    DocumentStatus,
    ExtractionResult,
    PipelineHealthResponse,
    WCAGFinding,
)
from services.extraction.adobe_client import AdobeExtractClient
from services.extraction.parser import parse_extraction_json

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
    title="Sacramento WCAG — Extraction Service",
    description=(
        "Consumes Pub/Sub push messages, downloads PDFs from GCS, "
        "runs Adobe Extract + Auto-Tag APIs, and queues results for AI drafting."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request model for the Pub/Sub push envelope
# ---------------------------------------------------------------------------


class PubSubPushEnvelope(BaseModel):
    """Pydantic model for the Pub/Sub push HTTP request body."""

    message: dict[str, Any]
    subscription: str = ""


# ---------------------------------------------------------------------------
# Status update helper (calls the ingestion service)
# ---------------------------------------------------------------------------


def _update_document_status(
    document_id: str,
    new_status: DocumentStatus,
    page_count: int | None = None,
) -> None:
    """Notify the ingestion service of a document status change.

    Non-fatal: logs errors but does not raise so extraction can continue.
    In production, this would be replaced with a shared database write.
    """
    import urllib.request

    params = f"new_status={new_status.value}"
    if page_count is not None:
        params += f"&page_count={page_count}"

    url = (
        f"{settings.ingestion_service_url}/api/documents/{document_id}/status?{params}"
    )
    try:
        req = urllib.request.Request(url, method="PATCH")
        with urllib.request.urlopen(req, timeout=10):
            pass
        logger.info("Updated document status: document_id=%s status=%s", document_id, new_status)
    except Exception as exc:
        logger.warning(
            "Failed to update document status for %s to %s: %s",
            document_id,
            new_status,
            exc,
        )


# ---------------------------------------------------------------------------
# Retry helper for the pipeline steps
# ---------------------------------------------------------------------------


def _run_with_retry(
    fn: Any,
    operation: str,
    max_retries: int,
    backoff_base: float,
) -> Any:
    """Run *fn()* with exponential backoff. Raises on final failure."""
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            wait = backoff_base ** (attempt - 1)
            logger.warning(
                "%s attempt %d/%d failed. Retrying in %.1fs. Error: %s",
                operation,
                attempt,
                max_retries,
                wait,
                exc,
            )
            time.sleep(wait)
    logger.error("%s failed after %d retries.", operation, max_retries)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Core extraction pipeline
# ---------------------------------------------------------------------------


def run_extraction_pipeline(document_id: str, gcs_input_path: str) -> None:
    """Execute the full extraction pipeline for a single document.

    Steps:
      1. Update document status -> EXTRACTING
      2. Call Adobe Extract API -> parse findings
      3. Call Adobe Auto-Tag API
      4. Store WCAGFindings JSON in GCS
      5. Publish to AI-drafting topic
      6. Update document status -> AI_DRAFTING

    Args:
        document_id: UUID of the document to process.
        gcs_input_path: gs:// URI of the source PDF.

    Raises:
        Exception: propagates any unrecoverable error after retries are
                   exhausted so the caller can NACK the Pub/Sub message.
    """
    if not document_id:
        raise ValueError("document_id must not be empty")
    if not gcs_input_path:
        raise ValueError("gcs_input_path must not be empty")

    logger.info(
        "Starting extraction pipeline: document_id=%s gcs_input_path=%s",
        document_id,
        gcs_input_path,
    )

    # Step 1: Mark as extracting
    _update_document_status(document_id, DocumentStatus.EXTRACTING)

    try:
        client = AdobeExtractClient()
    except (ValueError, RuntimeError) as exc:
        logger.error("Failed to initialise AdobeExtractClient: %s", exc)
        _update_document_status(document_id, DocumentStatus.FAILED)
        raise

    # Step 2: Adobe Extract API
    logger.info("Calling Adobe Extract API for document %s", document_id)
    extract_result: dict[str, Any] = _run_with_retry(
        fn=lambda: client.extract_pdf(gcs_input_path),
        operation="Adobe Extract API",
        max_retries=settings.max_retries,
        backoff_base=settings.retry_backoff_base,
    )
    logger.info(
        "Extract API complete: document_id=%s elements=%d images=%d tables=%d",
        document_id,
        extract_result.get("elements_count", 0),
        extract_result.get("images_count", 0),
        extract_result.get("tables_count", 0),
    )

    # Step 3: Adobe Auto-Tag API
    logger.info("Calling Adobe Auto-Tag API for document %s", document_id)
    auto_tag_result: dict[str, Any] = _run_with_retry(
        fn=lambda: client.auto_tag_pdf(gcs_input_path),
        operation="Adobe Auto-Tag API",
        max_retries=settings.max_retries,
        backoff_base=settings.retry_backoff_base,
    )
    logger.info(
        "Auto-Tag API complete: document_id=%s tags=%d",
        document_id,
        auto_tag_result.get("tag_count", 0),
    )

    # Build ExtractionResult
    extraction = ExtractionResult(
        document_id=document_id,
        adobe_job_id=extract_result.get("adobe_job_id", ""),
        extracted_json_path=extract_result["extracted_json_path"],
        auto_tag_json_path=auto_tag_result["auto_tag_json_path"],
        elements_count=extract_result.get("elements_count", 0),
        images_count=extract_result.get("images_count", 0),
        tables_count=extract_result.get("tables_count", 0),
    )

    # Step 4: Parse extracted JSON into WCAGFindings
    logger.info("Parsing extraction JSON for document %s", document_id)
    bucket_name, blob_name = gcs_client.parse_gcs_uri(extraction.extracted_json_path)
    raw_json_bytes = gcs_client.download_bytes(bucket_name, blob_name)
    extracted_json: dict[str, Any] = json.loads(raw_json_bytes)

    findings: list[WCAGFinding] = parse_extraction_json(document_id, extracted_json)
    logger.info(
        "Parsing complete: document_id=%s findings=%d", document_id, len(findings)
    )

    # Step 5: Store findings JSON in GCS
    findings_blob = f"extraction/{document_id}/wcag_findings.json"
    findings_json = json.dumps(
        [f.model_dump(mode="json") for f in findings], indent=2
    ).encode("utf-8")
    findings_gcs_path = gcs_client.upload_bytes(
        data=findings_json,
        bucket_name=settings.gcs_extraction_bucket,
        blob_name=findings_blob,
        content_type="application/json",
    )
    logger.info(
        "Stored %d findings to GCS: path=%s", len(findings), findings_gcs_path
    )

    # Step 6: Publish to AI-drafting topic
    logger.info("Publishing to AI-drafting topic for document %s", document_id)
    message_id = pubsub_client.publish_document_event(
        topic_name=settings.pubsub_ai_drafting_topic,
        document_id=document_id,
        extracted_json_path=extraction.extracted_json_path,
        auto_tag_json_path=extraction.auto_tag_json_path,
        findings_gcs_path=findings_gcs_path,
        findings_count=len(findings),
        images_count=extraction.images_count,
        tables_count=extraction.tables_count,
    )
    logger.info(
        "Published AI-drafting event: document_id=%s pubsub_message_id=%s",
        document_id,
        message_id,
    )

    # Step 7: Update status
    _update_document_status(document_id, DocumentStatus.AI_DRAFTING)
    logger.info(
        "Extraction pipeline complete: document_id=%s findings=%d",
        document_id,
        len(findings),
    )


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@app.post(
    "/api/extract",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Receive Pub/Sub push message and run extraction pipeline",
    description=(
        "Called by Cloud Pub/Sub push subscription. Parses the message envelope, "
        "extracts document_id and gcs_input_path, and runs the full extraction pipeline. "
        "Returns 204 on success (ACK), 4xx on bad input (NACK — do not retry), "
        "5xx on transient failure (NACK — allow retry)."
    ),
)
async def receive_pubsub_push(request: Request) -> None:
    # Parse raw body — Pub/Sub push may not set Content-Type to application/json
    try:
        body = await request.json()
    except Exception as exc:
        logger.error("Failed to parse request body as JSON: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Request body must be valid JSON: {exc}",
        ) from exc

    # Validate Pub/Sub envelope
    try:
        envelope = PubSubPushEnvelope.model_validate(body)
    except ValidationError as exc:
        logger.error("Invalid Pub/Sub push envelope: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Pub/Sub push envelope: {exc}",
        ) from exc

    # Decode inner payload
    try:
        payload = pubsub_client.parse_pubsub_push({"message": envelope.message})
    except Exception as exc:
        logger.error("Failed to decode Pub/Sub message data: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot decode Pub/Sub message payload: {exc}",
        ) from exc

    # Validate required fields
    document_id: str = payload.get("document_id", "").strip()
    gcs_input_path: str = payload.get("gcs_input_path", "").strip()

    if not document_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload missing required field: document_id",
        )
    if not gcs_input_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload missing required field: gcs_input_path",
        )

    logger.info(
        "Received extraction request: document_id=%s gcs_input_path=%s",
        document_id,
        gcs_input_path,
    )

    # Run pipeline — let 5xx propagate so Pub/Sub retries
    try:
        run_extraction_pipeline(document_id, gcs_input_path)
    except (ValueError, ValidationError) as exc:
        # Bad data — do not retry
        logger.error("Non-retryable extraction error for %s: %s", document_id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        # Transient — let Pub/Sub retry
        logger.exception("Extraction pipeline failed for document %s", document_id)
        _update_document_status(document_id, DocumentStatus.FAILED)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Extraction pipeline error: {exc}",
        ) from exc


@app.get(
    "/api/health",
    response_model=PipelineHealthResponse,
    tags=["health"],
    summary="Service health check",
)
def health_check() -> PipelineHealthResponse:
    """Returns 200 when the service is running."""
    return PipelineHealthResponse(
        status="healthy",
        services={"extraction": "up"},
    )


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Extraction service starting up")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("Extraction service shutting down")
