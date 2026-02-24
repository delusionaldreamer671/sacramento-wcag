"""AI Drafting Service — FastAPI application.

Receives Pub/Sub push messages from the extraction pipeline, loads extraction
results from GCS, calls Vertex AI to generate WCAG-compliant remediation
suggestions, creates HITLReviewItem records, and updates document status.

Endpoints:
  POST /api/draft   — Pub/Sub push handler; triggers AI drafting pipeline
  GET  /api/health  — Health check for Cloud Run liveness probe
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from services.common.config import settings
from services.common.gcs_client import (
    blob_exists,
    download_bytes,
    parse_gcs_uri,
    upload_bytes,
)
from services.common.models import (
    ComplexityFlag,
    DocumentStatus,
    ExtractionResult,
    HITLReviewItem,
    PipelineHealthResponse,
    WCAGCriterion,
    WCAGFinding,
)
from services.common.pubsub_client import publish_document_event
from services.ai_drafting.vertex_client import VertexAIClient, VertexAIError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="WCAG AI Drafting Service",
    description=(
        "Vertex AI / Gemini drafting service for Sacramento County PDF "
        "accessibility remediation pipeline."
    ),
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Shared client (initialised once at startup to reuse SDK connection)
# ---------------------------------------------------------------------------

_vertex_client: VertexAIClient | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _vertex_client  # noqa: PLW0603
    _vertex_client = VertexAIClient()
    logger.info("AI Drafting Service started. VertexAIClient ready.")


def _get_vertex_client() -> VertexAIClient:
    if _vertex_client is None:
        raise RuntimeError("VertexAIClient has not been initialised. Check startup event.")
    return _vertex_client


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PubSubMessage(BaseModel):
    """Inner Pub/Sub message object within the push envelope."""

    data: str = Field(description="Base64-encoded JSON payload")
    message_id: str = ""
    publish_time: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)


class PubSubPushEnvelope(BaseModel):
    """Cloud Run Pub/Sub push subscription HTTP envelope."""

    message: PubSubMessage
    subscription: str = ""


class DraftPayload(BaseModel):
    """Decoded payload expected inside the Pub/Sub message data field."""

    document_id: str
    extraction_result_path: str = Field(
        description="GCS path (gs://...) to the ExtractionResult JSON produced by the extraction service"
    )


class DraftResponse(BaseModel):
    document_id: str
    status: str
    review_items_created: int
    message: str


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _decode_pubsub_data(data_b64: str) -> dict[str, Any]:
    """Decode a base64 Pub/Sub data field into a Python dict.

    Raises:
        ValueError: If decoding or JSON parsing fails.
    """
    try:
        raw_bytes = base64.b64decode(data_b64)
        return json.loads(raw_bytes)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to decode Pub/Sub message data: {exc}") from exc


def _load_extraction_result(gcs_path: str) -> ExtractionResult:
    """Download and parse an ExtractionResult JSON from GCS.

    Args:
        gcs_path: gs://bucket/blob path to the ExtractionResult JSON.

    Returns:
        Validated ExtractionResult model instance.

    Raises:
        ValueError: If the GCS path is invalid or the object does not exist.
        ValidationError: If the JSON does not conform to ExtractionResult schema.
    """
    bucket_name, blob_name = parse_gcs_uri(gcs_path)

    if not blob_exists(bucket_name, blob_name):
        raise ValueError(
            f"ExtractionResult not found in GCS: gs://{bucket_name}/{blob_name}"
        )

    raw = download_bytes(bucket_name, blob_name)
    data = json.loads(raw)
    return ExtractionResult.model_validate(data)


def _load_elements_json(gcs_path: str) -> list[dict[str, Any]]:
    """Download the Adobe Extract JSON and return the elements array.

    The Adobe Extract API JSON has the form:
      {"elements": [...], "extended_metadata": {...}}

    Args:
        gcs_path: gs://bucket/blob path to the extracted JSON.

    Returns:
        List of element dicts from the "elements" key, or empty list if absent.
    """
    bucket_name, blob_name = parse_gcs_uri(gcs_path)
    if not blob_exists(bucket_name, blob_name):
        logger.warning("Elements JSON not found at %s; returning empty list", gcs_path)
        return []
    raw = download_bytes(bucket_name, blob_name)
    data = json.loads(raw)
    return data.get("elements", [])


def _determine_complexity(element: dict[str, Any]) -> ComplexityFlag:
    """Classify an extracted element into a ComplexityFlag.

    Rules (in order of precedence):
    - Tables with nesting_depth >= 2 → MANUAL
    - Tables with nesting_depth == 1 (merged cells) → REVIEW
    - Simple flat tables → REVIEW (always needs human validation)
    - Images with no surrounding text context → REVIEW
    - All other images → REVIEW (alt text requires human confirmation)
    - Headings → SIMPLE (auto-correction only, no HITL for minor level fixes)
    - Links → SIMPLE
    - Unknown element types → REVIEW (safe default)
    """
    element_type = str(element.get("type", "")).lower()
    nesting_depth = int(element.get("nesting_depth", 0))

    if "table" in element_type:
        if nesting_depth >= 2:
            return ComplexityFlag.MANUAL
        return ComplexityFlag.REVIEW

    if element_type in ("figure", "image"):
        return ComplexityFlag.REVIEW

    if element_type in ("h1", "h2", "h3", "h4", "h5", "h6", "heading"):
        return ComplexityFlag.SIMPLE

    if "link" in element_type:
        return ComplexityFlag.SIMPLE

    return ComplexityFlag.REVIEW


def _build_hitl_item(
    document_id: str,
    finding: WCAGFinding,
    element: dict[str, Any],
    ai_suggestion: str,
) -> HITLReviewItem:
    """Construct a HITLReviewItem from a WCAGFinding and its AI draft."""
    element_type = str(element.get("type", "unknown"))
    return HITLReviewItem(
        id=str(uuid.uuid4()),
        document_id=document_id,
        finding_id=finding.id,
        element_type=element_type,
        original_content={
            "element_id": element.get("element_id", ""),
            "type": element_type,
            "text": element.get("text", ""),
            "bounding_box": element.get("bounding_box", []),
            "page": element.get("page", 1),
            "attributes": element.get("attributes", {}),
        },
        ai_suggestion=ai_suggestion,
    )


# ---------------------------------------------------------------------------
# Core drafting pipeline
# ---------------------------------------------------------------------------


def _draft_image_element(
    document_id: str,
    element: dict[str, Any],
    surrounding_text: str,
    client: VertexAIClient,
) -> tuple[WCAGFinding, HITLReviewItem] | None:
    """Generate alt text for a single image element.

    Returns (WCAGFinding, HITLReviewItem) on success, or None on hard failure
    after all retries (the caller should flag the element as MANUAL instead).
    """
    element_id = str(element.get("element_id", str(uuid.uuid4())))
    complexity = _determine_complexity(element)

    finding = WCAGFinding(
        document_id=document_id,
        element_id=element_id,
        criterion=WCAGCriterion.ALT_TEXT,
        severity="serious",
        description="Image element is missing alt text (WCAG 1.1.1).",
        complexity=complexity,
    )

    try:
        image_context = {
            "element_type": element.get("type", "Figure"),
            "bounding_box": element.get("bounding_box", "unknown"),
            "page_number": int(element.get("page", 1)),
            "page_dimensions": element.get("page_dimensions", "unknown"),
            "additional_context": element.get("caption_text", ""),
        }
        ai_suggestion = client.generate_alt_text(image_context, surrounding_text)
        finding.ai_draft = ai_suggestion
        finding.suggested_fix = ai_suggestion
        item = _build_hitl_item(document_id, finding, element, ai_suggestion)
        return finding, item

    except VertexAIError as exc:
        logger.error(
            "VertexAIError generating alt text for element %s in doc %s: %s",
            element_id,
            document_id,
            exc,
        )
        finding.complexity = ComplexityFlag.MANUAL
        finding.description += " AI drafting failed — flagged for manual remediation."
        placeholder = "[MANUAL REMEDIATION REQUIRED — AI drafting failed]"
        finding.ai_draft = placeholder
        finding.suggested_fix = placeholder
        item = _build_hitl_item(document_id, finding, element, placeholder)
        return finding, item


def _draft_table_element(
    document_id: str,
    element: dict[str, Any],
    client: VertexAIClient,
) -> tuple[WCAGFinding, HITLReviewItem] | None:
    """Generate semantic HTML table structure for a single table element."""
    element_id = str(element.get("element_id", str(uuid.uuid4())))
    complexity = _determine_complexity(element)

    finding = WCAGFinding(
        document_id=document_id,
        element_id=element_id,
        criterion=WCAGCriterion.INFO_RELATIONSHIPS,
        severity="serious",
        description=(
            "Table element requires semantic HTML structure with proper "
            "header associations (WCAG 1.3.1)."
        ),
        complexity=complexity,
    )

    # MANUAL tables: skip AI, flag immediately for human remediation
    if complexity == ComplexityFlag.MANUAL:
        placeholder = (
            "[MANUAL REMEDIATION REQUIRED — Nested table (depth ≥ 2) "
            "requires human-authored semantic HTML]"
        )
        finding.ai_draft = placeholder
        finding.suggested_fix = placeholder
        item = _build_hitl_item(document_id, finding, element, placeholder)
        return finding, item

    try:
        table_data: dict[str, Any] = {
            "raw_table_data": element.get("raw_table_data", []),
            "column_headers": element.get("column_headers", []),
            "row_headers": element.get("row_headers", []),
            "table_id": element_id,
            "page_number": int(element.get("page", 1)),
            "rows": int(element.get("rows", 0)),
            "cols": int(element.get("cols", 0)),
            "has_column_headers": bool(element.get("has_column_headers", True)),
            "has_row_headers": bool(element.get("has_row_headers", False)),
            "nesting_depth": int(element.get("nesting_depth", 0)),
            "caption_text": str(element.get("caption_text", "")),
        }
        ai_suggestion = client.generate_table_structure(table_data)
        finding.ai_draft = ai_suggestion
        finding.suggested_fix = ai_suggestion
        item = _build_hitl_item(document_id, finding, element, ai_suggestion)
        return finding, item

    except VertexAIError as exc:
        logger.error(
            "VertexAIError generating table structure for element %s in doc %s: %s",
            element_id,
            document_id,
            exc,
        )
        finding.complexity = ComplexityFlag.MANUAL
        finding.description += " AI drafting failed — flagged for manual remediation."
        placeholder = "[MANUAL REMEDIATION REQUIRED — AI drafting failed]"
        finding.ai_draft = placeholder
        finding.suggested_fix = placeholder
        item = _build_hitl_item(document_id, finding, element, placeholder)
        return finding, item


def _draft_headings(
    document_id: str,
    heading_elements: list[dict[str, Any]],
    client: VertexAIClient,
) -> list[tuple[WCAGFinding, HITLReviewItem]]:
    """Analyse and correct heading hierarchy for all heading elements."""
    if not heading_elements:
        return []

    results: list[tuple[WCAGFinding, HITLReviewItem]] = []

    headings_input = [
        {
            "element_id": str(e.get("element_id", str(uuid.uuid4()))),
            "page_number": int(e.get("page", 1)),
            "level": int(e.get("heading_level", 2)),
            "text": str(e.get("text", "")),
        }
        for e in heading_elements
    ]

    try:
        corrected = client.generate_heading_structure(headings_input)
    except VertexAIError as exc:
        logger.error(
            "VertexAIError generating heading structure for doc %s: %s",
            document_id,
            exc,
        )
        # Fall back: create MANUAL items for every heading
        for elem in heading_elements:
            elem_id = str(elem.get("element_id", str(uuid.uuid4())))
            finding = WCAGFinding(
                document_id=document_id,
                element_id=elem_id,
                criterion=WCAGCriterion.HEADINGS_LABELS,
                severity="moderate",
                description=(
                    "Heading hierarchy analysis failed — flagged for manual review."
                ),
                complexity=ComplexityFlag.MANUAL,
                ai_draft="[MANUAL REMEDIATION REQUIRED — heading analysis failed]",
                suggested_fix="[MANUAL REMEDIATION REQUIRED — heading analysis failed]",
            )
            item = _build_hitl_item(
                document_id,
                finding,
                elem,
                "[MANUAL REMEDIATION REQUIRED — heading analysis failed]",
            )
            results.append((finding, item))
        return results

    # Map element_id → source element for original_content assembly
    elem_map = {
        str(e.get("element_id", "")): e for e in heading_elements
    }

    for correction in corrected:
        elem_id = str(correction.get("element_id", ""))
        flag = str(correction.get("flag", "OK"))
        original_level = int(correction.get("original_level", 2))
        corrected_level = int(correction.get("corrected_level", original_level))
        suggestion = correction.get("suggestion")

        if flag == "OK":
            # No issues — skip creating a HITL item for this heading
            continue

        severity = "moderate" if flag in ("LEVEL_CORRECTED", "NEEDS_REVIEW") else "minor"
        complexity = (
            ComplexityFlag.MANUAL if flag == "MANUAL"
            else ComplexityFlag.REVIEW if flag == "NEEDS_REVIEW"
            else ComplexityFlag.SIMPLE
        )

        ai_draft_parts = [
            f"Heading level corrected from H{original_level} to H{corrected_level}."
        ]
        if suggestion:
            ai_draft_parts.append(f"Suggested text: {suggestion}")
        ai_suggestion = " ".join(ai_draft_parts)

        finding = WCAGFinding(
            document_id=document_id,
            element_id=elem_id,
            criterion=WCAGCriterion.HEADINGS_LABELS,
            severity=severity,
            description=(
                f"Heading hierarchy issue (flag: {flag}). "
                f"Original level: H{original_level}, corrected: H{corrected_level}."
            ),
            complexity=complexity,
            ai_draft=ai_suggestion,
            suggested_fix=ai_suggestion,
        )

        source_element = elem_map.get(elem_id, {"element_id": elem_id, "type": "heading"})
        item = _build_hitl_item(document_id, finding, source_element, ai_suggestion)
        results.append((finding, item))

    return results


def _persist_review_items(
    document_id: str,
    items: list[HITLReviewItem],
    findings: list[WCAGFinding],
) -> str:
    """Serialise HITLReviewItems and WCAGFindings to GCS.

    Stores a single JSON object with both lists under the extraction bucket
    at: hitl-review/{document_id}/review_items.json

    Returns the GCS path where the data was stored.
    """
    payload = {
        "document_id": document_id,
        "created_at": _utcnow().isoformat(),
        "review_items": [item.model_dump(mode="json") for item in items],
        "findings": [finding.model_dump(mode="json") for finding in findings],
    }
    blob_name = f"hitl-review/{document_id}/review_items.json"
    data = json.dumps(payload, indent=2).encode("utf-8")
    upload_bytes(
        data=data,
        bucket_name=settings.gcs_extraction_bucket,
        blob_name=blob_name,
        content_type="application/json",
    )
    return f"gs://{settings.gcs_extraction_bucket}/{blob_name}"


def _run_drafting_pipeline(
    document_id: str,
    extraction_result: ExtractionResult,
    client: VertexAIClient,
) -> tuple[list[WCAGFinding], list[HITLReviewItem]]:
    """Execute the full AI drafting pipeline for one document.

    1. Load element list from GCS (Adobe Extract JSON).
    2. Partition elements by type (images, tables, headings).
    3. Generate AI suggestions for each category.
    4. Collect findings and HITL items.

    Args:
        document_id: UUID of the document being processed.
        extraction_result: Validated ExtractionResult from GCS.
        client: Initialised VertexAIClient.

    Returns:
        (findings, hitl_items) — parallel lists of results.
    """
    elements = _load_elements_json(extraction_result.extracted_json_path)

    images: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    headings: list[dict[str, Any]] = []

    # Build a text index: page → list of text strings, for context assembly
    page_text: dict[int, list[str]] = {}
    for elem in elements:
        page = int(elem.get("page", 1))
        text = str(elem.get("text", ""))
        if text:
            page_text.setdefault(page, []).append(text)

    for elem in elements:
        etype = str(elem.get("type", "")).lower()
        if etype in ("figure", "image"):
            images.append(elem)
        elif "table" in etype:
            tables.append(elem)
        elif any(
            etype.startswith(prefix)
            for prefix in ("h1", "h2", "h3", "h4", "h5", "h6", "heading")
        ):
            headings.append(elem)

    logger.info(
        "doc=%s: found %d images, %d tables, %d headings in %d total elements",
        document_id,
        len(images),
        len(tables),
        len(headings),
        len(elements),
    )

    all_findings: list[WCAGFinding] = []
    all_items: list[HITLReviewItem] = []

    # --- Images ---
    for elem in images:
        page = int(elem.get("page", 1))
        surrounding = " ".join(page_text.get(page, []))
        result = _draft_image_element(document_id, elem, surrounding, client)
        if result:
            finding, item = result
            all_findings.append(finding)
            all_items.append(item)

    # --- Tables ---
    for elem in tables:
        result = _draft_table_element(document_id, elem, client)
        if result:
            finding, item = result
            all_findings.append(finding)
            all_items.append(item)

    # --- Headings ---
    heading_pairs = _draft_headings(document_id, headings, client)
    for finding, item in heading_pairs:
        all_findings.append(finding)
        all_items.append(item)

    return all_findings, all_items


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health", response_model=PipelineHealthResponse)
async def health_check() -> PipelineHealthResponse:
    """Liveness probe for Cloud Run. Returns service health status."""
    vertex_status = "unknown"
    try:
        # Non-invasive check: confirm the client is initialised
        _get_vertex_client()
        vertex_status = "ok"
    except RuntimeError:
        vertex_status = "not_initialised"

    return PipelineHealthResponse(
        status="healthy",
        services={
            "vertex_ai": vertex_status,
            "gcs": "ok",
            "pubsub": "ok",
        },
    )


@app.post(
    "/api/draft",
    response_model=DraftResponse,
    status_code=status.HTTP_200_OK,
)
async def draft_document(request: Request) -> DraftResponse:
    """Handle Pub/Sub push message: run AI drafting pipeline for one document.

    Pub/Sub push format:
    {
      "message": {
        "data": "<base64-encoded JSON>",
        "message_id": "...",
        "publish_time": "..."
      },
      "subscription": "projects/.../subscriptions/..."
    }

    The decoded data must contain:
      - document_id (str): UUID of the document to process.
      - extraction_result_path (str): GCS path to the ExtractionResult JSON.

    Returns 200 on success (tells Pub/Sub to ACK the message).
    Returns 4xx/5xx on unrecoverable errors (Pub/Sub will NACK and retry).
    """
    # Parse envelope
    try:
        body = await request.json()
    except Exception as exc:
        logger.error("Failed to parse request JSON: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON body: {exc}",
        ) from exc

    try:
        envelope = PubSubPushEnvelope.model_validate(body)
    except ValidationError as exc:
        logger.error("Invalid Pub/Sub envelope: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Pub/Sub envelope: {exc}",
        ) from exc

    # Decode message data
    try:
        payload_dict = _decode_pubsub_data(envelope.message.data)
    except ValueError as exc:
        logger.error("Failed to decode Pub/Sub data: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Validate payload schema
    try:
        payload = DraftPayload.model_validate(payload_dict)
    except ValidationError as exc:
        logger.error("Invalid DraftPayload: %s | raw=%s", exc, payload_dict)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid payload schema: {exc}",
        ) from exc

    document_id = payload.document_id
    logger.info(
        "AI drafting started: document_id=%s extraction_path=%s",
        document_id,
        payload.extraction_result_path,
    )

    # Load extraction result
    try:
        extraction_result = _load_extraction_result(payload.extraction_result_path)
    except (ValueError, ValidationError) as exc:
        logger.error("Failed to load ExtractionResult for doc %s: %s", document_id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"ExtractionResult load failed: {exc}",
        ) from exc

    client = _get_vertex_client()

    # Run the pipeline — errors here are non-retryable per-element; the
    # pipeline itself handles element-level failures gracefully.
    try:
        findings, review_items = _run_drafting_pipeline(
            document_id=document_id,
            extraction_result=extraction_result,
            client=client,
        )
    except Exception as exc:
        logger.exception(
            "Unhandled error in drafting pipeline for doc %s", document_id
        )
        # Return 500 so Pub/Sub retries the message
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Drafting pipeline failed: {exc}",
        ) from exc

    # Persist review items to GCS
    try:
        review_items_path = _persist_review_items(
            document_id=document_id,
            items=review_items,
            findings=findings,
        )
    except Exception as exc:
        logger.exception(
            "Failed to persist review items for doc %s to GCS", document_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"GCS persist failed: {exc}",
        ) from exc

    # Publish to recompilation topic — downstream service waits for HITL
    # approval before recompiling; this event signals that HITL items are ready.
    try:
        publish_document_event(
            topic_name=settings.pubsub_recompilation_topic,
            document_id=document_id,
            status=DocumentStatus.HITL_REVIEW,
            review_items_count=len(review_items),
            review_items_path=review_items_path,
        )
    except Exception as exc:
        logger.error(
            "Failed to publish recompilation event for doc %s: %s",
            document_id,
            exc,
        )
        # Non-fatal: HITL items are already persisted to GCS.
        # Log and continue — the dashboard can poll GCS directly.

    manual_count = sum(
        1 for item in review_items
        if "[MANUAL REMEDIATION REQUIRED]" in item.ai_suggestion
    )

    logger.info(
        "AI drafting complete: doc=%s review_items=%d manual=%d gcs=%s",
        document_id,
        len(review_items),
        manual_count,
        review_items_path,
    )

    return DraftResponse(
        document_id=document_id,
        status=DocumentStatus.HITL_REVIEW,
        review_items_created=len(review_items),
        message=(
            f"AI drafting complete. {len(review_items)} review items created "
            f"({manual_count} flagged as MANUAL). "
            f"Stored at: {review_items_path}"
        ),
    )


# ---------------------------------------------------------------------------
# Global error handler — ensures unexpected exceptions return clean JSON
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error. See service logs for details."},
    )
