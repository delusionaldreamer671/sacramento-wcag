"""Recompilation service entry point.

Receives Pub/Sub push messages from the recompilation topic, loads all
approved HITL review items for a document, assembles them into semantic HTML,
generates a PDF/UA output using reportlab, validates accessibility, and
uploads the result to GCS.

Run locally:
    uvicorn services.recompilation.main:app --port 8003 --reload

Cloud Run:
    The container CMD is set to:
    uvicorn services.recompilation.main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from services.common import gcs_client
from services.common.config import settings
from services.common.models import (
    DocumentStatus,
    HITLReviewItem,
    PipelineHealthResponse,
    RemediatedDocument,
)
from services.recompilation.pdfua_builder import PDFUABuilder

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
    title="Sacramento WCAG — Recompilation Service",
    description=(
        "Receives approved HITL review items, assembles semantic HTML, "
        "generates PDF/UA output, validates accessibility, and stores the "
        "remediated document in GCS."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to dashboard origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory store for recompilation state (POC)
# Production: replace with Firestore / Cloud SQL
# ---------------------------------------------------------------------------

_recompilation_store: dict[str, RemediatedDocument] = {}

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PubSubEnvelope(BaseModel):
    """Cloud Run Pub/Sub push envelope.

    Cloud Pub/Sub delivers messages as HTTP POST with this structure::

        {
          "message": {
            "data": "<base64-encoded JSON>",
            "messageId": "...",
            "publishTime": "..."
          },
          "subscription": "projects/…/subscriptions/…"
        }
    """

    message: dict = Field(description="Pub/Sub message object containing base64-encoded data.")
    subscription: str = Field(default="", description="Full Pub/Sub subscription path.")


class RecompileRequest(BaseModel):
    """Direct HTTP trigger body for recompilation (used for testing)."""

    document_id: str = Field(description="UUID of the document to recompile.")
    document_title: Optional[str] = Field(
        default=None,
        description="Human-readable title for the output PDF. Defaults to document_id.",
    )

    @field_validator("document_id")
    @classmethod
    def document_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("document_id must be a non-empty string.")
        return v.strip()


class RecompilationStatusResponse(BaseModel):
    document_id: str
    status: str
    semantic_html_path: Optional[str] = None
    pdfua_output_path: Optional[str] = None
    axe_score: Optional[float] = None
    wcag_violations_remaining: int = 0
    manual_review_items: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# Internal HITL store (POC shim)
# In production this would be Firestore or a shared DB.
# The HITL dashboard writes items here; this service reads them.
# ---------------------------------------------------------------------------

_hitl_store: dict[str, list[HITLReviewItem]] = {}


def _get_hitl_items_for_document(document_id: str) -> list[HITLReviewItem]:
    """Retrieve all HITL review items for a given document.

    For the POC this reads from the in-memory _hitl_store.  In production
    this would query Firestore: ``db.collection("hitl_items").where("document_id", "==", document_id)``
    """
    return _hitl_store.get(document_id, [])


def _register_hitl_item(item: HITLReviewItem) -> None:
    """Register a HITL review item (used by tests and upstream services)."""
    if item.document_id not in _hitl_store:
        _hitl_store[item.document_id] = []
    _hitl_store[item.document_id].append(item)


# ---------------------------------------------------------------------------
# Core recompilation pipeline
# ---------------------------------------------------------------------------


def _map_hitl_item_to_element(
    item: HITLReviewItem,
) -> tuple[str, str, dict]:
    """Convert a HITLReviewItem into (element_type, content, attributes).

    The ``reviewer_edit`` overrides ``ai_suggestion`` when present (i.e. the
    reviewer corrected the AI draft).  ``original_content`` carries element
    metadata such as alt text placeholders, heading levels, and table data.
    """
    resolved_text = (item.reviewer_edit or item.ai_suggestion or "").strip()
    original = item.original_content or {}
    element_type = item.element_type.lower().strip()
    attributes: dict = {}

    if element_type == "heading":
        level = original.get("level", 2)
        try:
            level = int(level)
        except (ValueError, TypeError):
            level = 2
        attributes["level"] = max(1, min(6, level))
        return "heading", resolved_text, attributes

    if element_type == "image":
        # resolved_text IS the approved alt text
        attributes["alt"] = resolved_text
        attributes["src"] = original.get("src", "")
        caption = original.get("caption", "")
        return "image", caption, attributes

    if element_type == "table":
        attributes["headers"] = original.get("headers", [])
        attributes["rows"] = original.get("rows", [])
        # resolved_text used as caption if non-empty
        return "table", resolved_text, attributes

    if element_type == "list":
        attributes["items"] = original.get("items", [])
        attributes["ordered"] = bool(original.get("ordered", False))
        return "list", resolved_text, attributes

    if element_type == "link":
        attributes["href"] = original.get("href", "#")
        return "link", resolved_text, attributes

    # Default: paragraph
    return "paragraph", resolved_text, attributes


def _run_recompilation_pipeline(
    document_id: str,
    document_title: Optional[str] = None,
) -> RemediatedDocument:
    """Execute the full recompilation pipeline for a document.

    Steps:
    1. Load all HITLReviewItems for the document.
    2. Validate that no items are in a pending (unreviewed) state.
    3. Separate MANUAL items from auto-approvable items.
    4. Build PDFUABuilder and add each element.
    5. Generate semantic HTML.
    6. Validate accessibility of the HTML.
    7. Generate PDF/UA bytes.
    8. Upload HTML and PDF to GCS output bucket.
    9. Upload MANUAL_REVIEW_REQUIRED.csv if any manual items exist.
    10. Notify ingestion service to mark document as COMPLETE (best-effort).
    11. Return RemediatedDocument record.

    Args:
        document_id: The UUID of the document to recompile.
        document_title: Human-readable title for the PDF. Defaults to document_id.

    Returns:
        A ``RemediatedDocument`` Pydantic model.

    Raises:
        HTTPException 409: If any items are still pending review.
        HTTPException 422: If the document has no HITL items at all.
        HTTPException 502: If GCS upload fails after retries.
    """
    start_ts = time.monotonic()
    title = (document_title or document_id).strip()
    logger.info("Recompilation pipeline start: document_id=%s title=%r", document_id, title)

    # Step 1: Load items
    items = _get_hitl_items_for_document(document_id)
    if not items:
        logger.warning("No HITL items found for document_id=%s", document_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"No HITL review items found for document '{document_id}'. "
                "Ensure the AI drafting service has completed and items are registered."
            ),
        )

    # Step 2: Check for unreviewed items
    pending_items = [
        i for i in items if i.reviewer_decision is None
    ]
    if pending_items:
        logger.warning(
            "Recompilation blocked: document_id=%s pending_items=%d",
            document_id,
            len(pending_items),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot recompile document '{document_id}': "
                f"{len(pending_items)} item(s) are still pending review. "
                "Complete all HITL review items before recompiling."
            ),
        )

    # Step 3: Separate approved/edited items from rejected/manual items
    approved_items: list[HITLReviewItem] = []
    manual_items: list[HITLReviewItem] = []

    for item in items:
        decision = (item.reviewer_decision or "").lower()
        if decision in ("approve", "edit"):
            approved_items.append(item)
        elif decision == "reject":
            # Rejected items go to manual CSV — not included in output PDF
            manual_items.append(item)
        else:
            # Unknown decision: treat as manual
            logger.warning(
                "Unknown reviewer_decision '%s' for item_id=%s — treated as manual.",
                item.reviewer_decision,
                item.id,
            )
            manual_items.append(item)

    logger.info(
        "document_id=%s approved=%d manual/rejected=%d",
        document_id,
        len(approved_items),
        len(manual_items),
    )

    # Step 4: Build PDF/UA content
    builder = PDFUABuilder(document_id=document_id, document_title=title)

    for item in approved_items:
        try:
            elem_type, content, attributes = _map_hitl_item_to_element(item)
            builder.add_element(elem_type, content, attributes)
        except ValueError as exc:
            logger.warning(
                "Skipping malformed item_id=%s: %s", item.id, exc
            )

    # Step 5: Generate semantic HTML
    semantic_html = builder.build_semantic_html()

    # Step 6: Validate accessibility
    validation_result = builder.validate_accessibility(semantic_html)
    axe_score: float = float(validation_result.get("score", 0.0))
    violations: list[dict] = validation_result.get("violations", [])
    wcag_violations_remaining = len(violations)

    if violations:
        for v in violations:
            logger.warning(
                "Accessibility violation: document_id=%s criterion=%s severity=%s desc=%s",
                document_id,
                v.get("criterion"),
                v.get("severity"),
                v.get("description"),
            )

    # Step 7: Generate PDF/UA bytes
    try:
        pdf_bytes = builder.generate_pdfua(semantic_html)
    except Exception as exc:
        logger.exception("PDF generation failed for document_id=%s", document_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF generation failed: {exc}",
        ) from exc

    # Step 8: Upload HTML and PDF to GCS
    html_blob_name = f"output/{document_id}/{document_id}.html"
    pdf_blob_name = f"output/{document_id}/{document_id}_pdfua.pdf"

    semantic_html_gcs = _upload_with_retry(
        data=semantic_html.encode("utf-8"),
        bucket_name=settings.gcs_output_bucket,
        blob_name=html_blob_name,
        content_type="text/html; charset=utf-8",
        document_id=document_id,
        label="semantic HTML",
    )

    pdfua_gcs = _upload_with_retry(
        data=pdf_bytes,
        bucket_name=settings.gcs_output_bucket,
        blob_name=pdf_blob_name,
        content_type="application/pdf",
        document_id=document_id,
        label="PDF/UA",
    )

    # Step 9: Upload MANUAL_REVIEW_REQUIRED CSV if applicable
    if manual_items:
        csv_content = PDFUABuilder.generate_manual_review_csv(manual_items)
        csv_blob_name = f"output/{document_id}/MANUAL_REVIEW_REQUIRED.csv"
        _upload_with_retry(
            data=csv_content.encode("utf-8"),
            bucket_name=settings.gcs_output_bucket,
            blob_name=csv_blob_name,
            content_type="text/csv; charset=utf-8",
            document_id=document_id,
            label="MANUAL_REVIEW_REQUIRED CSV",
        )
        logger.info(
            "MANUAL_REVIEW_REQUIRED CSV uploaded: document_id=%s items=%d path=gs://%s/%s",
            document_id,
            len(manual_items),
            settings.gcs_output_bucket,
            csv_blob_name,
        )

    # Step 10: Notify ingestion service (best-effort)
    _notify_document_complete(
        document_id=document_id,
        gcs_output_path=pdfua_gcs,
    )

    elapsed = time.monotonic() - start_ts
    logger.info(
        "Recompilation pipeline complete: document_id=%s elapsed=%.2fs "
        "axe_score=%.2f violations=%d manual_items=%d",
        document_id,
        elapsed,
        axe_score,
        wcag_violations_remaining,
        len(manual_items),
    )

    result = RemediatedDocument(
        document_id=document_id,
        semantic_html_path=semantic_html_gcs,
        pdfua_output_path=pdfua_gcs,
        axe_score=axe_score,
        wcag_violations_remaining=wcag_violations_remaining,
        manual_review_items=len(manual_items),
    )
    _recompilation_store[document_id] = result
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _upload_with_retry(
    data: bytes,
    bucket_name: str,
    blob_name: str,
    content_type: str,
    document_id: str,
    label: str,
) -> str:
    """Upload bytes to GCS with exponential backoff retry.

    Args:
        data: Raw bytes to upload.
        bucket_name: GCS bucket name.
        blob_name: Blob path within the bucket.
        content_type: MIME type for the blob.
        document_id: For logging context.
        label: Human-readable label for log messages.

    Returns:
        The ``gs://`` URI of the uploaded object.

    Raises:
        HTTPException 502: After all retry attempts are exhausted.
    """
    max_retries = settings.max_retries
    backoff_base = settings.retry_backoff_base
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            uri = gcs_client.upload_bytes(
                data=data,
                bucket_name=bucket_name,
                blob_name=blob_name,
                content_type=content_type,
            )
            logger.info(
                "Uploaded %s: document_id=%s uri=%s attempt=%d",
                label,
                document_id,
                uri,
                attempt,
            )
            return uri
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                sleep_secs = backoff_base ** attempt
                logger.warning(
                    "GCS upload failed (%s, attempt %d/%d): %s — retrying in %.1fs",
                    label,
                    attempt,
                    max_retries,
                    exc,
                    sleep_secs,
                )
                time.sleep(sleep_secs)
            else:
                logger.exception(
                    "GCS upload failed (%s, attempt %d/%d) — giving up: %s",
                    label,
                    attempt,
                    max_retries,
                    exc,
                )

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"GCS upload of {label} failed after {max_retries} attempts: {last_exc}",
    )


def _notify_document_complete(
    document_id: str,
    gcs_output_path: str,
    timeout_seconds: int = 10,
) -> None:
    """Notify the ingestion service that a document has been fully recompiled.

    This is a best-effort call.  Failures are logged but do not abort the
    recompilation result.  The ingestion service PATCH endpoint updates the
    document status to COMPLETE and stores the output GCS path.

    Args:
        document_id: UUID of the document.
        gcs_output_path: GCS URI of the final PDF/UA output.
        timeout_seconds: HTTP request timeout.
    """
    url = (
        f"{settings.ingestion_service_url}/api/documents/{document_id}/status"
        f"?new_status=complete&gcs_output_path={gcs_output_path}"
    )
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.patch(url)
            response.raise_for_status()
            logger.info(
                "Notified ingestion service: document_id=%s new_status=complete",
                document_id,
            )
    except Exception as exc:
        logger.warning(
            "Failed to notify ingestion service (best-effort): document_id=%s error=%s",
            document_id,
            exc,
        )


def _parse_pubsub_envelope(body: dict) -> tuple[str, Optional[str]]:
    """Extract and validate document_id from a Pub/Sub push envelope.

    Args:
        body: The raw parsed JSON body of the HTTP POST from Pub/Sub.

    Returns:
        A tuple of (document_id, document_title).  document_title may be None
        if the message payload did not include one.

    Raises:
        HTTPException 400: If the envelope is malformed or document_id is absent.
    """
    message = body.get("message")
    if not message or not isinstance(message, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed Pub/Sub envelope: 'message' key missing or not a dict.",
        )

    data_b64 = message.get("data", "")
    if not data_b64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pub/Sub message has empty 'data' field.",
        )

    try:
        decoded = base64.b64decode(data_b64).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to decode Pub/Sub message data: {exc}",
        ) from exc

    document_id = payload.get("document_id", "").strip()
    if not document_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Pub/Sub message payload missing 'document_id'. "
                f"Received keys: {list(payload.keys())}"
            ),
        )

    document_title = payload.get("document_title") or None
    return document_id, document_title


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post(
    "/api/recompile",
    response_model=RecompilationStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger recompilation via Pub/Sub push or direct HTTP call",
    description=(
        "Accepts either a Pub/Sub push envelope (from Cloud Pub/Sub) or a "
        "``RecompileRequest`` JSON body (for direct invocation/testing). "
        "Runs the full recompilation pipeline and returns a status summary."
    ),
    tags=["recompilation"],
)
async def trigger_recompilation(request: Request) -> RecompilationStatusResponse:
    """Unified recompilation endpoint — handles both Pub/Sub push and direct HTTP."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not parse request body as JSON: {exc}",
        ) from exc

    # Detect payload type: Pub/Sub envelope has a "message" key;
    # direct RecompileRequest has "document_id" at the top level.
    if "message" in body:
        # Pub/Sub push envelope
        document_id, document_title = _parse_pubsub_envelope(body)
    elif "document_id" in body:
        # Direct HTTP trigger (testing / manual invocation)
        try:
            req = RecompileRequest(**body)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid RecompileRequest body: {exc}",
            ) from exc
        document_id = req.document_id
        document_title = req.document_title
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unrecognised request body. Expected either a Pub/Sub push envelope "
                "(with 'message' key) or a RecompileRequest (with 'document_id' key)."
            ),
        )

    logger.info(
        "Received recompilation trigger: document_id=%s title=%r",
        document_id,
        document_title,
    )

    result = _run_recompilation_pipeline(
        document_id=document_id,
        document_title=document_title,
    )

    message = (
        f"Recompilation complete for document '{document_id}'. "
        f"axe_score={result.axe_score:.2f}, "
        f"violations_remaining={result.wcag_violations_remaining}, "
        f"manual_items={result.manual_review_items}."
    )

    return RecompilationStatusResponse(
        document_id=document_id,
        status=DocumentStatus.COMPLETE.value,
        semantic_html_path=result.semantic_html_path,
        pdfua_output_path=result.pdfua_output_path,
        axe_score=result.axe_score,
        wcag_violations_remaining=result.wcag_violations_remaining,
        manual_review_items=result.manual_review_items,
        message=message,
    )


@app.get(
    "/api/recompile/{document_id}/status",
    response_model=RecompilationStatusResponse,
    summary="Get recompilation status for a document",
    description=(
        "Returns the latest recompilation result for a given document_id, "
        "including GCS paths, axe_score, violation count, and manual item count."
    ),
    tags=["recompilation"],
)
def get_recompilation_status(document_id: str) -> RecompilationStatusResponse:
    if not document_id or not document_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="document_id must be a non-empty string.",
        )

    result = _recompilation_store.get(document_id.strip())
    if result is None:
        # Check if there are HITL items for this document — gives better error context
        items = _get_hitl_items_for_document(document_id)
        if items:
            pending = sum(1 for i in items if i.reviewer_decision is None)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No recompilation record found for document '{document_id}'. "
                    f"Document has {len(items)} HITL item(s) ({pending} pending). "
                    "POST /api/recompile to begin recompilation."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No recompilation record found for document '{document_id}'. "
                "The document may not have been processed yet."
            ),
        )

    status_label = DocumentStatus.COMPLETE.value
    message = (
        f"Recompilation complete. "
        f"axe_score={result.axe_score:.2f if result.axe_score is not None else 'N/A'}, "
        f"violations={result.wcag_violations_remaining}, "
        f"manual_items={result.manual_review_items}."
    )

    return RecompilationStatusResponse(
        document_id=document_id,
        status=status_label,
        semantic_html_path=result.semantic_html_path,
        pdfua_output_path=result.pdfua_output_path,
        axe_score=result.axe_score,
        wcag_violations_remaining=result.wcag_violations_remaining,
        manual_review_items=result.manual_review_items,
        message=message,
    )


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
        services={"recompilation": "up"},
    )


# ---------------------------------------------------------------------------
# Internal: register HITL item (called by HITL dashboard / ai_drafting service)
# ---------------------------------------------------------------------------


@app.post(
    "/api/internal/hitl-items",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Register a HITL review item (internal)",
    description=(
        "Internal endpoint used by the HITL dashboard and AI drafting service "
        "to register review items for a document. "
        "Not exposed to the public internet — IAP or internal-only routing required."
    ),
    tags=["internal"],
)
def register_hitl_item(item: HITLReviewItem) -> dict:
    """Register a HITL review item in the in-memory store."""
    _register_hitl_item(item)
    logger.info(
        "Registered HITL item: item_id=%s document_id=%s element_type=%s decision=%s",
        item.id,
        item.document_id,
        item.element_type,
        item.reviewer_decision,
    )
    return {
        "item_id": item.id,
        "document_id": item.document_id,
        "message": "HITL item registered successfully.",
    }


@app.get(
    "/api/internal/hitl-items/{document_id}",
    response_model=list[HITLReviewItem],
    summary="List HITL items for a document (internal)",
    tags=["internal"],
)
def list_hitl_items(document_id: str) -> list[HITLReviewItem]:
    """List all registered HITL review items for a document."""
    if not document_id or not document_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="document_id must be a non-empty string.",
        )
    return _get_hitl_items_for_document(document_id.strip())


# ---------------------------------------------------------------------------
# Startup / shutdown events
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Recompilation service starting up")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("Recompilation service shutting down")
