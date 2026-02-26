"""FastAPI router for PDF ingestion endpoints.

Handles document upload, status retrieval, and listing.
Document metadata is persisted to SQLite via the database module.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from services.common import gcs_client, pubsub_client
from services.common.config import settings
from services.common.constants import API_V1_PREFIX
from services.common.database import get_db
from services.common.ir import BlockType, IRDocument, ValidationMode
from services.ingestion.converter import ValidationBlockedError
from services.common.models import (
    DocumentStatus,
    DocumentStatusResponse,
    DocumentUploadResponse,
    PDFDocument,
)
from services.common.wcag_checker import run_full_audit, findings_to_proposals, audit_summary_dict
from services.common.wcag_rules import WCAG_RULES_LEDGER
from services.common.wcag_techniques import get_techniques_for_criterion, get_failures_for_criterion

logger = logging.getLogger(__name__)

router = APIRouter(prefix=API_V1_PREFIX)


# ---------------------------------------------------------------------------
# Concurrency guard
# ---------------------------------------------------------------------------


async def _acquire_pipeline_semaphore() -> None:
    """Acquire the pipeline concurrency semaphore atomically or raise 503.

    Uses asyncio.wait_for with timeout=0.0 to perform a non-blocking atomic
    acquire.  The old pattern (check sem._value > 0 then return) was a classic
    TOCTOU race: another coroutine could acquire the semaphore between the
    check and the point where the caller actually tried to use it, and the
    semaphore was never actually acquired by this code path.

    Callers that use this function are responsible for releasing the semaphore
    (e.g. via a try/finally or a context manager at the call site).  For new
    code, prefer `async with get_pipeline_semaphore():` directly.
    """
    from services.ingestion.main import get_pipeline_semaphore

    sem = get_pipeline_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=0.0)
    except (asyncio.TimeoutError, TimeoutError):
        raise HTTPException(
            status_code=503,
            detail="Server at maximum document processing capacity. Retry later.",
            headers={"Retry-After": "30"},
        )


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------


def _db():
    """Return the module-level Database singleton."""
    return get_db(settings.db_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_document_or_404(document_id: str) -> dict:
    doc = _db().get_document(document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )
    return doc


def _validate_pdf(file: UploadFile) -> None:
    """Raise 400 if the uploaded file is not a PDF.

    Checks (in order):
    1. Content-type header must be application/pdf or application/octet-stream.
    2. Filename must end with .pdf.

    Magic-byte validation (%%PDF signature) is intentionally deferred to
    _validate_pdf_bytes() which is called after the body has been read, because
    UploadFile is a streaming object and reading it here would consume the body.
    """
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported content type '{file.content_type}'. Only PDFs are accepted.",
        )
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File '{filename}' does not have a .pdf extension.",
        )


def _validate_pdf_bytes(contents: bytes, filename: str) -> None:
    """Raise 400 if bytes do not contain the PDF magic signature (%%PDF).

    Checks the first 1024 bytes for the %%PDF marker (accounting for a
    possible BOM prefix).  Corrupt or non-PDF files that pass content-type /
    extension checks are caught here before being sent to Adobe Extract, which
    would produce a confusing 502 error.
    """
    from services.common.security import validate_pdf_bytes

    try:
        validate_pdf_bytes(contents, filename)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


# Minimum percentage of pages that must contain extractable text.
# Below this threshold the PDF is likely scanned/image-only and
# requires OCR pre-processing which is not yet available.
_SCANNED_PDF_TEXT_THRESHOLD = 0.10  # 10%
_MIN_CHARS_PER_PAGE = 50  # Fewer than this = "no meaningful text"


def _check_scanned_pdf(pdf_bytes: bytes, filename: str) -> None:
    """Reject scanned/image-only PDFs that lack extractable text.

    Uses pypdf to attempt text extraction on every page. If fewer than
    10% of pages contain meaningful text (>50 chars), the PDF is likely
    a scanned document that requires OCR — which this pipeline does not
    yet support. Raises HTTP 422 with a clear explanation.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.debug("pypdf not available — skipping scanned-PDF detection")
        return

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        total_pages = len(reader.pages)
        if total_pages == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="PDF contains zero pages.",
            )

        pages_with_text = 0
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
                # Strip whitespace and check for meaningful content
                if len(text.strip()) >= _MIN_CHARS_PER_PAGE:
                    pages_with_text += 1
            except Exception:
                # If extraction fails for a page, count it as no-text
                pass

        text_ratio = pages_with_text / total_pages

        logger.info(
            "Scanned-PDF check: filename=%s pages=%d pages_with_text=%d ratio=%.1f%%",
            filename, total_pages, pages_with_text, text_ratio * 100,
        )

        if text_ratio < _SCANNED_PDF_TEXT_THRESHOLD:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"This PDF appears to be a scanned document ({pages_with_text} of "
                    f"{total_pages} pages contain extractable text). "
                    f"The remediation pipeline requires PDFs with selectable/extractable "
                    f"text. Scanned or image-only documents need OCR pre-processing, "
                    f"which is not yet available in this system. Please provide a "
                    f"text-based PDF, or run OCR on the document first using a tool "
                    f"like Adobe Acrobat Pro or Google Document AI."
                ),
            )
    except HTTPException:
        raise  # Re-raise our own HTTPExceptions
    except Exception as exc:
        # Non-fatal — if the check fails, let the pipeline proceed
        logger.warning(
            "Scanned-PDF check failed for %s (%s) — proceeding anyway",
            filename, exc,
        )


# ---------------------------------------------------------------------------
# Deterministic proposal ID helpers
# ---------------------------------------------------------------------------


def _make_proposal_id(prefix: str, page: int, index: int) -> str:
    """Generate a deterministic proposal ID from element coordinates."""
    return f"{prefix}_p{page}_i{index}"


def _make_structural_id(category: str, index: int) -> str:
    """Generate a deterministic proposal ID for structural issues."""
    return f"str_{category}_{index}"


# ---------------------------------------------------------------------------
# Upload endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/documents/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a PDF document for remediation",
    description=(
        "Accepts a PDF file, stores it in the GCS input bucket, "
        "creates a PDFDocument record, and publishes a message to the "
        "extraction Pub/Sub topic to begin processing."
    ),
)
async def upload_document(
    file: UploadFile = File(..., description="PDF file to remediate"),
) -> DocumentUploadResponse:
    _validate_pdf(file)

    filename: str = file.filename or "unknown.pdf"

    # Create a temporary PDFDocument to obtain a stable UUID before DB insert
    _tmp = PDFDocument(filename=filename, gcs_input_path="")
    doc_id = _tmp.id

    blob_name = f"input/{doc_id}/{filename}"

    # Write upload to a temp file then stream to GCS
    with tempfile.TemporaryDirectory() as tmp_dir:
        local_path = Path(tmp_dir) / filename
        contents = await file.read()
        if not contents:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )
        # MEDIUM-1.14: Reject corrupt PDFs before they reach Adobe Extract (400 instead of 502)
        _validate_pdf_bytes(contents, filename)
        local_path.write_bytes(contents)
        logger.info(
            "Received upload: filename=%s size=%d bytes document_id=%s",
            filename,
            len(contents),
            doc_id,
        )

        try:
            gcs_uri = gcs_client.upload_file(
                local_path=local_path,
                bucket_name=settings.gcs_input_bucket,
                blob_name=blob_name,
                content_type="application/pdf",
            )
        except Exception as exc:
            logger.exception("GCS upload failed for document %s", doc_id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to upload file to storage: {exc}",
            ) from exc

    doc = _db().insert_document(
        doc_id=doc_id,
        filename=filename,
        status=DocumentStatus.QUEUED.value,
        gcs_input_path=gcs_uri,
    )
    logger.info("Created document record: document_id=%s gcs_path=%s", doc_id, gcs_uri)

    # Publish to extraction topic
    try:
        message_id = pubsub_client.publish_document_event(
            topic_name=settings.pubsub_extraction_topic,
            document_id=doc_id,
            filename=filename,
            gcs_input_path=gcs_uri,
        )
        logger.info(
            "Published extraction event: document_id=%s pubsub_message_id=%s",
            doc_id,
            message_id,
        )
    except Exception as exc:
        # Mark as failed — we cannot proceed without queueing
        _db().update_document_status(doc_id, DocumentStatus.FAILED.value)
        logger.exception("Pub/Sub publish failed for document %s", doc_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"File uploaded but failed to queue for processing: {exc}",
        ) from exc

    return DocumentUploadResponse(
        document_id=doc["id"],
        status=doc["status"],
        message=f"Document '{filename}' accepted and queued for extraction.",
    )


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/documents/{document_id}",
    response_model=DocumentStatusResponse,
    summary="Get document processing status",
)
def get_document_status(document_id: str) -> DocumentStatusResponse:
    doc = _get_document_or_404(document_id)
    return DocumentStatusResponse(
        document_id=doc["id"],
        filename=doc["filename"],
        status=doc["status"],
        page_count=doc["page_count"],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/documents",
    response_model=list[DocumentStatusResponse],
    summary="List all documents with pagination",
)
def list_documents(
    skip: int = Query(default=0, ge=0, description="Number of documents to skip"),
    limit: int = Query(
        default=20, ge=1, le=200, description="Maximum documents to return"
    ),
    status_filter: Optional[DocumentStatus] = Query(
        default=None, alias="status", description="Filter by document status"
    ),
) -> list[DocumentStatusResponse]:
    status_value = status_filter.value if status_filter is not None else None
    docs = _db().list_documents(skip=skip, limit=limit, status=status_value)

    logger.info(
        "list_documents: skip=%d limit=%d status=%s returned=%d",
        skip,
        limit,
        status_value,
        len(docs),
    )

    return [
        DocumentStatusResponse(
            document_id=d["id"],
            filename=d["filename"],
            status=d["status"],
            page_count=d["page_count"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )
        for d in docs
    ]


# ---------------------------------------------------------------------------
# Internal: status update (called by downstream services via internal API)
# ---------------------------------------------------------------------------


@router.patch(
    "/documents/{document_id}/status",
    response_model=DocumentStatusResponse,
    summary="Update document processing status (internal)",
    description="Used by downstream pipeline services to update document status.",
)
def update_document_status(
    document_id: str,
    new_status: DocumentStatus,
    page_count: Optional[int] = Query(default=None, ge=0),
    gcs_output_path: Optional[str] = Query(default=None),
) -> DocumentStatusResponse:
    # Ensure the document exists before attempting an update
    _get_document_or_404(document_id)

    # ---------------------------------------------------------------------------
    # Mandatory HITL gate — block transition to COMPLETE without human review
    # ---------------------------------------------------------------------------
    if new_status is DocumentStatus.COMPLETE:
        review_items = _db().get_review_items(document_id)
        if review_items:
            pending = [
                item for item in review_items
                if item.get("reviewer_decision") is None
            ]
            if pending:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Document cannot be marked complete without human review. "
                        f"{len(pending)} item(s) still pending review."
                    ),
                )
        else:
            # No review items — simple document (no HITL findings generated).
            # Allow the transition but log a warning so operators are aware.
            logger.warning(
                "HITL gate: document %s has no review items — allowing COMPLETE "
                "transition without human review. Verify this is a simple document "
                "with no WCAG findings requiring remediation.",
                document_id,
            )

    kwargs: dict = {}
    if page_count is not None:
        kwargs["page_count"] = page_count
    if gcs_output_path is not None:
        kwargs["gcs_output_path"] = gcs_output_path

    doc = _db().update_document_status(document_id, new_status.value, **kwargs)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    logger.info(
        "Updated document status: document_id=%s new_status=%s",
        document_id,
        new_status,
    )

    return DocumentStatusResponse(
        document_id=doc["id"],
        filename=doc["filename"],
        status=doc["status"],
        page_count=doc["page_count"],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


# ---------------------------------------------------------------------------
# Direct convert endpoint (synchronous — no Pub/Sub or GCS needed)
# ---------------------------------------------------------------------------


@router.post(
    "/convert",
    summary="Upload and convert a PDF to accessible HTML, PDF, or ZIP",
    description=(
        "Synchronous endpoint that accepts a PDF file and returns a "
        "WCAG 2.1 AA compliant HTML or PDF/UA document (or a ZIP archive "
        "containing both). Runs the full pipeline (Adobe Extract → AI alt text "
        "→ auto-approve → build output) in a single request. Designed for "
        "local testing."
    ),
)
async def convert_document(
    file: UploadFile = File(..., description="PDF file to remediate"),
    output_format: Literal["html", "pdf", "zip"] = Query(
        default="html", description="Output format: html, pdf, or zip"
    ),
    validation_mode: str = Query(
        default="publish", description="Validation strictness: draft or publish"
    ),
) -> Response:
    _validate_pdf(file)

    contents = await file.read()
    if not contents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    filename = file.filename or "document.pdf"
    # MEDIUM-1.14: Reject corrupt PDFs before running full pipeline
    _validate_pdf_bytes(contents, filename)
    stem = Path(filename).stem

    try:
        mode = ValidationMode(validation_mode)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid validation_mode '{validation_mode}'. Must be 'draft' or 'publish'.",
        )

    logger.info(
        "Convert request: filename=%s size=%d bytes format=%s validation_mode=%s",
        filename,
        len(contents),
        output_format,
        mode,
    )

    from services.ingestion.converter import convert_pdf_sync
    from services.ingestion.main import get_pipeline_semaphore

    task_id = ""
    sem = get_pipeline_semaphore()

    if output_format == "zip":
        # CRITICAL-1.4: ZIP output runs convert_pdf_sync twice (once for HTML,
        # once for PDF) against the SAME source file, then bundles both into a
        # ZIP archive.  This is intentional — the two passes use different
        # output renderers and must be kept independent.
        #
        # Design constraint: because each call runs Adobe Extract + AI alt text
        # independently, there is a cost multiplier of 2x for ZIP output.  A
        # future optimisation would share the extraction/IR stage and only fork
        # at the HTML/PDF build stage.  Until then this constraint is documented
        # here so callers understand why ZIP output costs twice as much as a
        # single-format request.
        #
        # Dedup guard: `contents` (the raw bytes) is identical for both calls —
        # the same source PDF is processed twice.  This is not a bug (the two
        # calls intentionally use different output_format values), but if a
        # caller accidentally passes the same output_format for both calls the
        # result would be identical files in the ZIP.  That is prevented by the
        # hard-coded "html" / "pdf" literals below.
        try:
            async with sem:
                html_bytes, _, task_id, _ = await asyncio.to_thread(
                    convert_pdf_sync,
                    contents,
                    filename,
                    "html",
                    validation_mode=mode,
                )
                pdf_bytes, _, _, _ = await asyncio.to_thread(
                    convert_pdf_sync,
                    contents,
                    filename,
                    "pdf",
                    validation_mode=mode,
                )
        except ValidationBlockedError as exc:
            logger.warning(
                "Validation blocked output for %s: %s", filename, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": str(exc),
                    "violations": exc.violations,
                },
            ) from exc
        except Exception as exc:
            logger.exception("Conversion failed for %s", filename)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Conversion failed: {exc}",
            ) from exc

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{stem}_remediated.html", html_bytes)
            zf.writestr(f"{stem}_remediated.pdf", pdf_bytes)
        output_bytes = buf.getvalue()
        content_type = "application/zip"
        disposition = f'attachment; filename="{stem}_remediated.zip"'
    else:
        try:
            async with sem:
                output_bytes, content_type, task_id, _ = await asyncio.to_thread(
                    convert_pdf_sync,
                    contents,
                    filename,
                    output_format,
                    validation_mode=mode,
                )
        except ValidationBlockedError as exc:
            logger.warning(
                "Validation blocked output for %s: %s", filename, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": str(exc),
                    "violations": exc.violations,
                },
            ) from exc
        except Exception as exc:
            logger.exception("Conversion failed for %s", filename)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Conversion failed: {exc}",
            ) from exc

        disposition = f'attachment; filename="{stem}_remediated.{output_format}"'

    return Response(
        content=output_bytes,
        media_type=content_type,
        headers={
            "Content-Disposition": disposition,
            "X-Task-Id": task_id,
            "Access-Control-Expose-Headers": "X-Task-Id",
        },
    )


# ---------------------------------------------------------------------------
# Pipeline telemetry endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/telemetry",
    summary="Get pipeline telemetry records",
    description=(
        "Returns per-document pipeline telemetry records including timing, "
        "extraction metrics, AI metrics, validation results, and output details. "
        "Newest records first."
    ),
)
def get_telemetry(
    limit: int = Query(default=50, ge=1, le=500, description="Max records to return"),
    status_filter: Optional[str] = Query(
        default=None, alias="status",
        description="Filter by status: running, success, failed, error",
    ),
) -> dict:
    records = _db().list_telemetry(limit=limit, status=status_filter)
    return {"records": records, "count": len(records)}


# ---------------------------------------------------------------------------
# Baseline Validation
# ---------------------------------------------------------------------------


@router.get(
    "/documents/{task_id}/baseline",
    summary="Get VeraPDF baseline validation for a task",
    description=(
        "Returns the VeraPDF PDF/UA-1 baseline validation results captured "
        "before remediation. Returns 404 if no baseline exists (e.g. VeraPDF "
        "was unavailable during processing)."
    ),
)
def get_baseline_validation(task_id: str) -> dict:
    result = _db().get_baseline_validation(task_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No baseline validation found for task {task_id}",
        )
    return result


# ---------------------------------------------------------------------------
# Analysis models
# ---------------------------------------------------------------------------

_GENERIC_ALT_RE = re.compile(
    r"^\[Figure on page .+ — alt text requires review\]$"
)

_SEVERITY_SCORE = {"critical": 4, "serious": 3, "moderate": 2, "minor": 1}


def _compute_review_priority(proposal: dict) -> int:
    """Compute review priority score for a proposal.

    Higher score = needs attention first.
    Formula: severity * 2 + (auto_fixable penalty) + (action_type weight)
    """
    severity = _SEVERITY_SCORE.get(proposal.get("severity", "moderate"), 2)
    auto_fixable = proposal.get("auto_fixable", False)
    action_type = proposal.get("action_type", "auto_fix")

    # Auto-fixable items need less attention
    auto_penalty = 0 if auto_fixable else 2

    # Manual review items need more attention
    action_weight = 0
    if action_type == "manual_review":
        action_weight = 3
    elif action_type == "ai_draft":
        action_weight = 1

    return severity * 2 + auto_penalty + action_weight


class AnalysisProposal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: str
    rule_name: str = ""
    wcag_criterion: str
    element_type: str
    element_id: str
    image_id: str | None = None
    description: str
    proposed_fix: str
    severity: str
    page: int
    auto_fixable: bool
    action_type: str = "auto_fix"  # "auto_fix", "ai_draft", "manual_review"
    review_priority: int = 0       # Higher = needs attention first
    technique_refs: str = ""       # PDF technique references (e.g. "PDF1, PDF4")


class AnalysisSummary(BaseModel):
    total_issues: int = 0
    critical: int = 0
    serious: int = 0
    moderate: int = 0
    warning: int = 0
    auto_fixable: int = 0
    needs_review: int = 0
    # New: audit coverage metrics
    rules_checked: int = 0
    rules_passed: int = 0
    rules_failed: int = 0
    rules_not_applicable: int = 0
    rules_errored: int = 0
    coverage_pct: float = 0.0
    rules_breakdown: list[dict] = Field(default_factory=list)


class AltTextProposalResponse(BaseModel):
    id: str
    image_id: str = ""
    block_id: str = ""
    page_num: int = 0
    original_alt: str = ""
    proposed_alt: str = ""
    image_classification: str = "informative"
    confidence: float = 0.0
    status: str = "pending"
    reviewer_decision: str | None = None
    reviewer_edit: str | None = None


class AnalysisResult(BaseModel):
    task_id: str
    filename: str
    page_count: int
    proposals: list[AnalysisProposal]
    summary: AnalysisSummary
    alt_text_proposals: list[AltTextProposalResponse] = Field(default_factory=list)
    pipeline_metadata: dict = Field(default_factory=dict)


def _analyze_ir_document(ir_doc: IRDocument) -> list[AnalysisProposal]:
    """Walk an IRDocument and identify WCAG gaps, returning proposals."""
    proposals: list[AnalysisProposal] = []

    all_blocks = ir_doc.all_blocks()

    # --- 1. Missing alt text (WCAG 1.1.1) — per-image with deterministic IDs ---
    images = [b for b in all_blocks if b.block_type == BlockType.IMAGE]
    page_img_counters: dict[int, int] = {}
    for img in images:
        alt = img.attributes.get("alt", "")
        if not alt or _GENERIC_ALT_RE.match(alt):
            # Use the image_id set during extraction if available; otherwise derive one
            image_id = img.attributes.get("image_id", "")
            if not image_id:
                img_idx = page_img_counters.get(img.page_num, 0)
                page_img_counters[img.page_num] = img_idx + 1
                image_id = _make_proposal_id("img", img.page_num, img_idx)
            proposals.append(AnalysisProposal(
                id=image_id,
                category="alt_text",
                wcag_criterion="1.1.1",
                element_type="image",
                element_id=image_id,
                description=(
                    f"Image '{image_id}' on page {img.page_num} lacks descriptive alt text"
                ),
                proposed_fix="AI will generate contextual alt text based on surrounding content",
                severity="critical",
                page=img.page_num,
                auto_fixable=True,
                action_type="ai_draft",
            ))

    # --- 2. Heading hierarchy (WCAG 2.4.6) ---
    headings = [b for b in all_blocks if b.block_type == BlockType.HEADING]
    if not headings and ir_doc.page_count > 1:
        # Multi-page document with no headings at all
        proposals.append(AnalysisProposal(
            id=_make_structural_id("heading_hierarchy", 0),
            category="heading_hierarchy",
            wcag_criterion="2.4.6",
            element_type="document",
            element_id="document-level",
            description="Document has no headings — screen readers cannot navigate by structure",
            proposed_fix="Headings will be inferred from text formatting (font size, weight)",
            severity="serious",
            page=1,
            auto_fixable=True,
        ))
    else:
        # Check for skipped heading levels (e.g., h1 -> h3)
        prev_level = 0
        hdg_idx = 0
        for h in headings:
            level = h.attributes.get("level", 1)
            if isinstance(level, str):
                try:
                    level = int(level)
                except ValueError:
                    level = 1
            if prev_level > 0 and level > prev_level + 1:
                proposals.append(AnalysisProposal(
                    id=_make_proposal_id("hdg", h.page_num, hdg_idx),
                    category="heading_hierarchy",
                    wcag_criterion="2.4.6",
                    element_type="heading",
                    element_id=h.block_id,
                    description=(
                        f"Heading level skips from h{prev_level} to h{level} "
                        f"on page {h.page_num}"
                    ),
                    proposed_fix=f"Heading will be adjusted to h{prev_level + 1} to maintain hierarchy",
                    severity="serious",
                    page=h.page_num,
                    auto_fixable=True,
                ))
                hdg_idx += 1
            prev_level = level

    # --- 3. Table structure (WCAG 1.3.1) ---
    tables = [b for b in all_blocks if b.block_type == BlockType.TABLE]
    page_tbl_counters: dict[int, int] = {}
    for tbl in tables:
        headers = tbl.attributes.get("headers", [])
        rows = tbl.attributes.get("rows", [])
        row_count = len(rows) if isinstance(rows, list) else 0
        if not headers:
            tbl_idx = page_tbl_counters.get(tbl.page_num, 0)
            page_tbl_counters[tbl.page_num] = tbl_idx + 1
            # Determine complexity
            is_complex = row_count > 20
            proposals.append(AnalysisProposal(
                id=_make_proposal_id("tbl", tbl.page_num, tbl_idx),
                category="table_structure",
                wcag_criterion="1.3.1",
                element_type="table",
                element_id=tbl.block_id,
                description=(
                    f"Table on page {tbl.page_num} has no header associations "
                    f"({row_count} rows)"
                ),
                proposed_fix=(
                    "First row will be designated as header row with proper <th> markup"
                    if not is_complex
                    else "Complex table — headers will be inferred but may need manual review"
                ),
                severity="serious" if not is_complex else "critical",
                page=tbl.page_num,
                auto_fixable=not is_complex,
            ))

    # --- 4. Language tag (WCAG 3.1.1) ---
    # The pipeline always sets the language tag, but we report it as a
    # proposal so the user sees it as a remediation step.
    proposals.append(AnalysisProposal(
        id=_make_structural_id("language", 0),
        category="language",
        wcag_criterion="3.1.1",
        element_type="document",
        element_id="document-lang",
        description="Document language tag will be set to ensure assistive technology reads content correctly",
        proposed_fix=f"Language attribute will be set to '{ir_doc.language}'",
        severity="moderate",
        page=1,
        auto_fixable=True,
    ))

    # --- 5. Reading order (WCAG 1.3.2) ---
    # Detect multi-column layouts or pages with many overlapping bboxes
    reading_order_idx = 0
    for page in ir_doc.pages:
        blocks = page.blocks
        if len(blocks) < 2:
            continue
        # Simple heuristic: if blocks have bboxes that overlap horizontally
        # (different columns) this may indicate reading order issues
        x_positions = sorted(set(
            round(b.bbox.x1, -1) for b in blocks if b.bbox.x1 > 0
        ))
        if len(x_positions) >= 3:
            proposals.append(AnalysisProposal(
                id=_make_structural_id("reading_order", reading_order_idx),
                category="reading_order",
                wcag_criterion="1.3.2",
                element_type="page",
                element_id=f"page-{page.page_num}",
                description=(
                    f"Page {page.page_num} appears to have a multi-column layout "
                    f"— reading order may need verification"
                ),
                proposed_fix="Reading order will be set based on extracted element sequence",
                severity="moderate",
                page=page.page_num,
                auto_fixable=True,
            ))
            reading_order_idx += 1

    return proposals


# ---------------------------------------------------------------------------
# Structural HTML analysis (Deep Analyzer)
# ---------------------------------------------------------------------------


def _structural_fix_text(category: str) -> str:
    """Return a human-readable proposed fix for a structural violation category."""
    fixes = {
        "table_caption": "Table captions will be synthesized from surrounding context",
        "table_headers": "First row will be designated as header with scope attributes",
        "skip_navigation": "Skip navigation link will be added to document structure",
        "landmark": "Main landmark element will be added to document structure",
        "heading_hierarchy": "Heading levels will be adjusted to maintain proper hierarchy",
        "language": "Language attribute will be set on the HTML element",
        "document_title": "Document title will be set from filename",
        "placeholder_alt_text": "AI will generate descriptive alt text to replace placeholders",
        "missing_image_src": "Missing image sources will be reconstructed from PDF extraction",
    }
    return fixes.get(category, "This structural issue will be addressed during remediation")


def _analyze_html_structural(ir_doc: IRDocument, filename: str) -> list[AnalysisProposal]:
    """Run a dry-run HTML build + validate_accessibility to find structural WCAG gaps."""
    from services.ingestion.converter import stage_build_html

    proposals: list[AnalysisProposal] = []

    try:
        html_content, builder = stage_build_html(ir_doc, title=filename)
        val_result = builder.validate_accessibility(html_content, mode=ValidationMode.DRAFT)
    except Exception:
        # If HTML build fails, that's a finding itself
        proposals.append(AnalysisProposal(
            category="structural",
            wcag_criterion="4.1.1",
            element_type="document",
            element_id="html-build-failure",
            description="HTML build failed — document structure may be too complex for automated remediation",
            proposed_fix="Manual review required to assess document structure",
            severity="critical",
            page=0,
            auto_fixable=False,
            action_type="manual_review",
        ))
        return proposals

    violations = val_result.get("violations", [])
    seen_descriptions: set[str] = set()
    structural_counters: dict[str, int] = {}

    for v in violations:
        desc = v.get("description", "")
        criterion = v.get("criterion", "")
        severity = v.get("severity", "moderate")
        vclass = v.get("violation_class", "warning")

        # Skip duplicates
        if desc in seen_descriptions:
            continue
        seen_descriptions.add(desc)

        # Map to category and action_type
        category = "structural"
        action_type = "auto_fix"

        if "caption" in desc.lower():
            category = "table_caption"
        elif "scope" in desc.lower() or "<th" in desc.lower():
            category = "table_headers"
        elif "skip" in desc.lower() or "navigation" in desc.lower():
            category = "skip_navigation"
        elif "landmark" in desc.lower() or "<main" in desc.lower():
            category = "landmark"
        elif "heading" in desc.lower():
            category = "heading_hierarchy"
        elif "lang" in desc.lower():
            category = "language"
        elif "title" in desc.lower():
            category = "document_title"
        elif "alt" in desc.lower() and "placeholder" in desc.lower():
            category = "placeholder_alt_text"
            action_type = "ai_draft"
        elif "alt" in desc.lower():
            category = "alt_text"
            action_type = "ai_draft"
        elif "src" in desc.lower():
            category = "missing_image_src"

        # Don't duplicate proposals already covered by IR-level analysis
        # (images missing alt text are already identified per-image in IR analysis)
        if category in ("alt_text",) and "missing" in desc.lower():
            continue

        cat_idx = structural_counters.get(category, 0)
        structural_counters[category] = cat_idx + 1

        proposals.append(AnalysisProposal(
            id=_make_structural_id(category, cat_idx),
            category=category,
            wcag_criterion=criterion or "1.3.1",
            element_type="document",
            element_id=f"structural-{len(proposals)}",
            description=desc,
            proposed_fix=_structural_fix_text(category),
            severity=severity,
            page=0,
            auto_fixable=(action_type == "auto_fix"),
            action_type=action_type,
        ))

    return proposals


# ---------------------------------------------------------------------------
# Analyze endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/analyze",
    response_model=AnalysisResult,
    summary="Analyze a PDF for WCAG accessibility issues",
    description=(
        "Uploads a PDF, extracts its structure, and identifies WCAG 2.1 AA "
        "gaps without applying any remediations. Returns a list of proposals "
        "that the user can review before choosing to remediate."
    ),
)
async def analyze_document(
    file: UploadFile = File(..., description="PDF file to analyze"),
) -> AnalysisResult:
    _validate_pdf(file)

    contents = await file.read()
    if not contents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    filename = file.filename or "document.pdf"
    # MEDIUM-1.14: Reject corrupt PDFs before running full pipeline
    _validate_pdf_bytes(contents, filename)
    _check_scanned_pdf(contents, filename)

    logger.info(
        "Analyze request: filename=%s size=%d bytes",
        filename,
        len(contents),
    )

    from services.ingestion.converter import stage_extract, stage_ai_alt_text
    from services.ingestion.main import get_pipeline_semaphore
    from services.common.pipeline import StageSpec, PipelineMetadata, run_stage

    STAGE_EXTRACT = StageSpec(name="extract", category="required")
    STAGE_AI_ALT_TEXT = StageSpec(name="ai_alt_text", category="required_with_fallback")
    STAGE_AUDIT = StageSpec(name="wcag_audit", category="required")

    pipeline = PipelineMetadata(task_id="")

    try:
        async with get_pipeline_semaphore():
            extract_result = await asyncio.to_thread(
                run_stage, STAGE_EXTRACT, stage_extract, contents, filename,
            )
            pipeline.record_stage(extract_result)
            if not extract_result.ok:
                raise RuntimeError(f"Extraction failed: {'; '.join(extract_result.errors)}")
            ir_doc = extract_result.data
    except Exception as exc:
        logger.exception("Extraction failed for %s", filename)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF extraction failed: {exc}",
        ) from exc

    # Apply deterministic fixes BEFORE AI alt text
    from services.common.deterministic_remediator import apply_deterministic_fixes
    STAGE_DETERMINISTIC = StageSpec(name="deterministic_fixes", category="required")
    det_result = await asyncio.to_thread(
        run_stage, STAGE_DETERMINISTIC, apply_deterministic_fixes, ir_doc,
    )
    pipeline.record_stage(det_result)
    if det_result.ok and det_result.data:
        ir_doc, _det_fixes = det_result.data

    # Generate AI alt text for images BEFORE auditing — populates alt attributes
    ai_result = await asyncio.to_thread(
        run_stage, STAGE_AI_ALT_TEXT, stage_ai_alt_text, ir_doc,
    )
    pipeline.record_stage(ai_result)
    if ai_result.data is not None:
        ir_doc = ai_result.data
    if not ai_result.ok:
        logger.warning(
            "AI alt text generation %s for %s: %s",
            ai_result.status, filename, ai_result.warnings,
        )

    # Run the full WCAG 2.1 AA audit — all 50 criteria
    audit_result = run_full_audit(ir_doc)

    logger.info(
        "WCAG audit complete: filename=%s rules_checked=%d passed=%d failed=%d na=%d errored=%d coverage=%.1f%%",
        filename,
        audit_result.rules_checked,
        audit_result.rules_passed,
        audit_result.rules_failed,
        audit_result.rules_not_applicable,
        audit_result.rules_errored,
        audit_result.coverage_pct,
    )

    # Convert FAIL findings to frontend-compatible proposals
    proposal_dicts = findings_to_proposals(audit_result.findings)

    # B7: Priority scoring — score each proposal and sort by priority
    for p in proposal_dicts:
        p["review_priority"] = _compute_review_priority(p)
    proposal_dicts.sort(key=lambda p: p.get("review_priority", 0), reverse=True)

    proposals = [AnalysisProposal(**p) for p in proposal_dicts]

    # B2: Validator aggregation — cross-reference internal audit with available validators
    from services.common.validator_aggregator import aggregate_validation_results
    internal_results: dict[str, str] = {}
    for f in audit_result.findings:
        key = f.criterion
        if key not in internal_results:
            internal_results[key] = f.status.value
        elif f.status.value == "fail":
            internal_results[key] = "fail"

    aggregated = aggregate_validation_results(internal_results=internal_results)
    validation_confidence = {
        "high": sum(1 for a in aggregated if a.confidence.value == "high"),
        "medium": sum(1 for a in aggregated if a.confidence.value == "medium"),
        "low": sum(1 for a in aggregated if a.confidence.value == "low"),
        "needs_human_review": sum(1 for a in aggregated if a.needs_human_review),
    }

    # Build summary from audit
    summary_data = audit_summary_dict(audit_result)

    task_id = str(uuid.uuid4())
    pipeline.task_id = task_id

    # Store audit as baseline for pre/post comparison (B6)
    pipeline_dict = pipeline.to_dict()
    pipeline_dict["baseline_audit"] = {
        "rules_checked": audit_result.rules_checked,
        "rules_passed": audit_result.rules_passed,
        "rules_failed": audit_result.rules_failed,
        "coverage_pct": audit_result.coverage_pct,
    }
    pipeline_dict["validation_confidence"] = validation_confidence

    # Generate alt text proposals for HITL review (if enabled)
    alt_text_proposals: list[AltTextProposalResponse] = []
    if settings.alt_text_hitl_enabled:
        alt_text_proposals = _generate_alt_text_proposals(ir_doc, task_id)

    # MEDIUM-1.11: IR persistence design constraint
    # The analyze endpoint builds an IRDocument in memory and discards it after
    # returning findings.  The IR is NOT persisted to storage.  When the client
    # subsequently calls /remediate with the same PDF, the pipeline runs the
    # full extraction stage again from scratch (Adobe Extract → IR build).
    #
    # Consequence: the task_id returned here is NOT usable as a handle for a
    # pre-computed IR.  Analyze and remediate are fully independent operations.
    #
    # Future optimisation (TODO): persist the IR to GCS or SQLite keyed by
    # content-hash of the source PDF, and have /remediate reuse it when the
    # same file is submitted within a configurable TTL window.  This would
    # eliminate the double extraction cost for the analyze → remediate workflow.

    return AnalysisResult(
        task_id=task_id,
        filename=filename,
        page_count=ir_doc.page_count,
        proposals=proposals,
        summary=AnalysisSummary(**summary_data),
        alt_text_proposals=alt_text_proposals,
        pipeline_metadata=pipeline_dict,
    )


# ---------------------------------------------------------------------------
# Alt Text Proposal Helpers & Endpoints
# ---------------------------------------------------------------------------

_GENERIC_ALT_RE_ROUTER = re.compile(
    r"^\[Figure on page .+ — alt text requires review\]$"
)


def _generate_alt_text_proposals(
    ir_doc: IRDocument,
    task_id: str,
) -> list[AltTextProposalResponse]:
    """Extract alt-text proposals from IMAGE blocks and persist to DB."""
    db = _db()
    proposals: list[AltTextProposalResponse] = []

    all_blocks = ir_doc.all_blocks()
    for block in all_blocks:
        if block.block_type != BlockType.IMAGE:
            continue

        alt = block.attributes.get("alt", "")
        image_id = block.attributes.get("image_id", "")
        classification = block.attributes.get("data-complexity", "informative")

        # Skip decorative images
        if block.attributes.get("aria-hidden") == "true":
            continue

        # Determine if this needs review
        is_generic = bool(_GENERIC_ALT_RE_ROUTER.match(alt)) if alt else True
        has_ai_alt = bool(alt) and not is_generic

        # Create proposal for images with AI-generated or placeholder alt text
        original_alt = "" if is_generic else alt
        proposed_alt = alt if has_ai_alt else ""
        confidence = 0.8 if has_ai_alt else 0.0

        try:
            pid = db.insert_alt_text_proposal(
                task_id=task_id,
                document_id=ir_doc.document_id,
                image_id=image_id,
                block_id=block.block_id,
                page_num=block.page_num,
                original_alt=original_alt,
                proposed_alt=proposed_alt,
                image_classification=classification,
                confidence=confidence,
            )
            proposals.append(AltTextProposalResponse(
                id=pid,
                image_id=image_id,
                block_id=block.block_id,
                page_num=block.page_num,
                original_alt=original_alt,
                proposed_alt=proposed_alt,
                image_classification=classification,
                confidence=confidence,
            ))
        except Exception:
            logger.warning(
                "Failed to create alt text proposal for block %s",
                block.block_id, exc_info=True,
            )

    logger.info(
        "Generated %d alt text proposals for task %s", len(proposals), task_id,
    )
    return proposals


@router.get(
    "/documents/{task_id}/alt-text-proposals",
    summary="Get alt text proposals for a task",
)
def get_alt_text_proposals(task_id: str) -> dict:
    rows = _db().get_alt_text_proposals(task_id)
    return {"task_id": task_id, "proposals": rows, "count": len(rows)}


class AltTextDecisionRequest(BaseModel):
    decision: str = Field(description="approve, edit, or reject")
    reviewer_edit: Optional[str] = None
    reviewed_by: Optional[str] = None


@router.post(
    "/alt-text-proposals/{proposal_id}/decision",
    summary="Submit a reviewer decision on an alt text proposal",
)
def submit_alt_text_decision(
    proposal_id: str,
    body: AltTextDecisionRequest,
) -> dict:
    if body.decision not in ("approve", "edit", "reject"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid decision '{body.decision}'. Must be approve, edit, or reject.",
        )
    result = _db().update_alt_text_proposal_decision(
        proposal_id=proposal_id,
        decision=body.decision,
        reviewer_edit=body.reviewer_edit,
        reviewed_by=body.reviewed_by,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alt text proposal {proposal_id} not found",
        )
    return result


class BatchAltTextApproveRequest(BaseModel):
    proposal_ids: list[str] = Field(min_length=1)
    reviewed_by: Optional[str] = None


@router.post(
    "/alt-text-proposals/batch-approve",
    summary="Batch-approve multiple alt text proposals",
)
def batch_approve_alt_text(body: BatchAltTextApproveRequest) -> dict:
    count = _db().batch_approve_alt_text_proposals(
        proposal_ids=body.proposal_ids,
        reviewed_by=body.reviewed_by,
    )
    return {"approved_count": count}


# ---------------------------------------------------------------------------
# Remediate endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/remediate",
    summary="Apply approved remediations and return accessible document",
    description=(
        "Accepts a PDF file and an optional list of approved proposal IDs, "
        "runs the full remediation pipeline applying only approved changes, "
        "and returns the accessible output. If approved_ids is omitted or "
        "empty, all remediations are applied (backward-compatible)."
    ),
)
async def remediate_document(
    file: UploadFile = File(..., description="PDF file to remediate"),
    output_format: Literal["html", "pdf"] = Query(
        default="html", description="Output format: html or pdf"
    ),
    validation_mode: str = Query(
        default="draft", description="Validation strictness: draft or publish"
    ),
    approved_ids: str = Form(default=""),
) -> Response:
    _validate_pdf(file)

    contents = await file.read()
    if not contents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    filename = file.filename or "document.pdf"
    # MEDIUM-1.14: Reject corrupt PDFs before running full pipeline
    _validate_pdf_bytes(contents, filename)
    _check_scanned_pdf(contents, filename)
    stem = Path(filename).stem

    try:
        mode = ValidationMode(validation_mode)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid validation_mode '{validation_mode}'. Must be 'draft' or 'publish'.",
        )

    # Parse approved_ids JSON array
    approved_set: set[str] | None = None
    if approved_ids.strip():
        try:
            parsed = json.loads(approved_ids)
            if isinstance(parsed, list) and len(parsed) > 0:
                approved_set = set(parsed)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="approved_ids must be a valid JSON array",
            )

    logger.info(
        "Remediate request: filename=%s size=%d bytes format=%s validation_mode=%s approved_ids=%s",
        filename,
        len(contents),
        output_format,
        mode,
        len(approved_set) if approved_set else "all",
    )

    from services.ingestion.converter import convert_pdf_sync
    from services.ingestion.main import get_pipeline_semaphore

    # MEDIUM-9.26: delta header placeholder — emitted on both success and error paths
    _partial_delta_headers: dict[str, str] = {
        "Access-Control-Expose-Headers": "X-Task-Id, X-Pipeline-Metadata, X-Remediation-Delta",
    }

    try:
        async with get_pipeline_semaphore():
            output_bytes, content_type, task_id, pipeline = await asyncio.to_thread(
                convert_pdf_sync,
                contents,
                filename,
                output_format,
                validation_mode=mode,
                approved_ids=approved_set,
            )
    except ValidationBlockedError as exc:
        logger.warning(
            "Unexpected validation block for %s: %s", filename, exc,
        )
        # MEDIUM-9.26: include partial delta so clients know pipeline errored
        _partial_delta_headers["X-Remediation-Delta"] = json.dumps({
            "status": "blocked",
            "before_failed": None,
            "before_passed": None,
            "after_failed": None,
            "after_passed": None,
        })
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": str(exc),
                "violations": exc.violations,
            },
            headers=_partial_delta_headers,
        ) from exc
    except Exception as exc:
        logger.exception("Remediation failed for %s", filename)
        # MEDIUM-9.26: include partial delta so clients know pipeline errored
        _partial_delta_headers["X-Remediation-Delta"] = json.dumps({
            "status": "error",
            "before_failed": None,
            "before_passed": None,
            "after_failed": None,
            "after_passed": None,
        })
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Remediation failed: {exc}",
            headers=_partial_delta_headers,
        ) from exc

    disposition = f'attachment; filename="{stem}_remediated.{output_format}"'

    # HIGH-9.14: Compute a true remediation delta that includes before_* values.
    # The pipeline exposes two audit stages:
    #   - "pre_remediation_audit"  (before fixes are applied — may be absent in
    #     some converter versions; None means "not captured this run")
    #   - "post_remediation_audit" (after fixes are applied)
    # Without before_* the delta is meaningless, so we always emit all four
    # fields, using None when a stage result was not available.
    pipeline_dict = pipeline.to_dict()
    stages = pipeline_dict.get("stages", [])

    pre_audit_stage = next(
        (s for s in stages if s.get("stage_name") == "pre_remediation_audit"),
        None,
    )
    post_audit_stage = next(
        (s for s in stages if s.get("stage_name") == "post_remediation_audit"),
        None,
    )

    before_failed: int | None = None
    before_passed: int | None = None
    after_failed: int | None = None
    after_passed: int | None = None

    if pre_audit_stage and pre_audit_stage.get("metadata"):
        pre_meta = pre_audit_stage["metadata"]
        before_failed = pre_meta.get("rules_failed")
        before_passed = pre_meta.get("rules_passed")

    if post_audit_stage and post_audit_stage.get("metadata"):
        post_meta = post_audit_stage["metadata"]
        after_failed = post_meta.get("rules_failed")
        after_passed = post_meta.get("rules_passed")

    # Always include the delta header when we have at least post-audit data
    remediation_delta: dict = {}
    if after_failed is not None or after_passed is not None:
        remediation_delta = {
            "before_failed": before_failed,
            "before_passed": before_passed,
            "after_failed": after_failed,
            "after_passed": after_passed,
        }

    response_headers = {
        "Content-Disposition": disposition,
        "X-Task-Id": task_id,
        "X-Pipeline-Metadata": json.dumps(pipeline_dict),
        "Access-Control-Expose-Headers": "X-Task-Id, X-Pipeline-Metadata, X-Remediation-Delta",
    }
    if remediation_delta:
        response_headers["X-Remediation-Delta"] = json.dumps(remediation_delta)

    return Response(
        content=output_bytes,
        media_type=content_type,
        headers=response_headers,
    )


# ---------------------------------------------------------------------------
# WCAG Rules reference endpoint (public, no auth)
# ---------------------------------------------------------------------------


class TechniqueRef(BaseModel):
    id: str
    title: str
    technique_type: str
    pdf_structure: str
    check_description: str


class FailureTechniqueRef(BaseModel):
    id: str
    title: str
    description: str
    pdf_implication: str


class WCAGRuleResponse(BaseModel):
    criterion: str
    name: str
    level: str
    principle: str
    guideline: str
    description: str
    pdf_applicability: str
    automation: str
    default_severity: str
    default_remediation: str
    condition: str
    pdf_techniques: list[TechniqueRef]
    failure_techniques: list[FailureTechniqueRef]


@router.get(
    "/wcag-rules",
    summary="List all 50 WCAG 2.1 AA rules with technique cross-references",
    response_model=list[WCAGRuleResponse],
)
async def list_wcag_rules() -> list[WCAGRuleResponse]:
    """Return all 50 WCAG 2.1 Level A + AA rules with PDF technique cross-references.

    No authentication required — this is a reference endpoint.
    """
    results: list[WCAGRuleResponse] = []
    for rule in WCAG_RULES_LEDGER:
        pdf_techs = [
            TechniqueRef(
                id=t.id,
                title=t.title,
                technique_type=t.technique_type,
                pdf_structure=t.pdf_structure,
                check_description=t.check_description,
            )
            for t in get_techniques_for_criterion(rule.criterion)
        ]
        fail_techs = [
            FailureTechniqueRef(
                id=ft.id,
                title=ft.title,
                description=ft.description,
                pdf_implication=ft.pdf_implication,
            )
            for ft in get_failures_for_criterion(rule.criterion)
        ]
        results.append(WCAGRuleResponse(
            criterion=rule.criterion,
            name=rule.name,
            level=rule.level.value,
            principle=rule.principle.value,
            guideline=rule.guideline,
            description=rule.description,
            pdf_applicability=rule.pdf_applicability.value,
            automation=rule.automation.value,
            default_severity=rule.default_severity.value,
            default_remediation=rule.default_remediation.value,
            condition=rule.condition,
            pdf_techniques=pdf_techs,
            failure_techniques=fail_techs,
        ))
    return results


# ---------------------------------------------------------------------------
# WCAG Coverage Matrix & Content-Type Matrix
# ---------------------------------------------------------------------------


@router.get(
    "/wcag/coverage-matrix",
    summary="Full WCAG 2.1 AA coverage matrix with technique cross-references",
)
async def get_coverage_matrix() -> list[dict]:
    """Return one entry per criterion with automation level, techniques, and applicability."""
    from services.common.coverage_matrix import generate_coverage_matrix
    return generate_coverage_matrix()


@router.get(
    "/wcag/coverage-summary",
    summary="Aggregate coverage statistics",
)
async def get_coverage_summary() -> dict:
    """Return summary counts by level, automation, applicability, and remediation."""
    from services.common.coverage_matrix import coverage_summary
    return coverage_summary()


@router.get(
    "/wcag/content-type-matrix",
    summary="Automation vs human review matrix by content type",
)
async def get_content_type_matrix() -> list[dict]:
    """Return breakdown of automated, AI-assisted, and human actions per content type."""
    from services.common.coverage_matrix import generate_content_type_matrix
    return generate_content_type_matrix()
