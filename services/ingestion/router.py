"""FastAPI router for PDF ingestion endpoints.

Handles document upload, status retrieval, and listing.
Document metadata is persisted to SQLite via the database module.
"""

from __future__ import annotations

import asyncio
import io
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
from services.common.database import get_db
from services.common.ir import BlockType, IRDocument
from services.ingestion.converter import ValidationBlockedError
from services.common.models import (
    DocumentStatus,
    DocumentStatusResponse,
    DocumentUploadResponse,
    PDFDocument,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


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
    """Raise 400 if the uploaded file is not a PDF."""
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
) -> Response:
    _validate_pdf(file)

    contents = await file.read()
    if not contents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    filename = file.filename or "document.pdf"
    stem = Path(filename).stem
    logger.info(
        "Convert request: filename=%s size=%d bytes format=%s",
        filename,
        len(contents),
        output_format,
    )

    from services.ingestion.converter import convert_pdf_sync

    task_id = ""

    if output_format == "zip":
        # Generate both HTML and PDF, then bundle them into a ZIP archive.
        try:
            html_bytes, _, task_id = await asyncio.to_thread(
                convert_pdf_sync,
                contents,
                filename,
                "html",
            )
            pdf_bytes, _, _ = await asyncio.to_thread(
                convert_pdf_sync,
                contents,
                filename,
                "pdf",
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
            output_bytes, content_type, task_id = await asyncio.to_thread(
                convert_pdf_sync,
                contents,
                filename,
                output_format,
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
# Analysis models
# ---------------------------------------------------------------------------

_GENERIC_ALT_RE = re.compile(
    r"^\[Figure on page .+ — alt text requires review\]$"
)


class AnalysisProposal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: str
    wcag_criterion: str
    element_type: str
    element_id: str
    description: str
    proposed_fix: str
    severity: str
    page: int
    auto_fixable: bool


class AnalysisSummary(BaseModel):
    total_issues: int = 0
    critical: int = 0
    serious: int = 0
    moderate: int = 0
    auto_fixable: int = 0
    needs_review: int = 0


class AnalysisResult(BaseModel):
    task_id: str
    filename: str
    page_count: int
    proposals: list[AnalysisProposal]
    summary: AnalysisSummary


def _analyze_ir_document(ir_doc: IRDocument) -> list[AnalysisProposal]:
    """Walk an IRDocument and identify WCAG gaps, returning proposals."""
    proposals: list[AnalysisProposal] = []

    all_blocks = ir_doc.all_blocks()

    # --- 1. Missing alt text (WCAG 1.1.1) ---
    images = [b for b in all_blocks if b.block_type == BlockType.IMAGE]
    for img in images:
        alt = img.attributes.get("alt", "")
        if not alt or _GENERIC_ALT_RE.match(alt):
            proposals.append(AnalysisProposal(
                category="alt_text",
                wcag_criterion="1.1.1",
                element_type="image",
                element_id=img.block_id,
                description=f"Image on page {img.page_num} has no descriptive alt text",
                proposed_fix="AI will generate contextual alt text based on surrounding content",
                severity="critical",
                page=img.page_num,
                auto_fixable=True,
            ))

    # --- 2. Heading hierarchy (WCAG 2.4.6) ---
    headings = [b for b in all_blocks if b.block_type == BlockType.HEADING]
    if not headings and ir_doc.page_count > 1:
        # Multi-page document with no headings at all
        proposals.append(AnalysisProposal(
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
        for h in headings:
            level = h.attributes.get("level", 1)
            if isinstance(level, str):
                try:
                    level = int(level)
                except ValueError:
                    level = 1
            if prev_level > 0 and level > prev_level + 1:
                proposals.append(AnalysisProposal(
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
            prev_level = level

    # --- 3. Table structure (WCAG 1.3.1) ---
    tables = [b for b in all_blocks if b.block_type == BlockType.TABLE]
    for tbl in tables:
        headers = tbl.attributes.get("headers", [])
        rows = tbl.attributes.get("rows", [])
        row_count = len(rows) if isinstance(rows, list) else 0
        if not headers:
            # Determine complexity
            is_complex = row_count > 20
            proposals.append(AnalysisProposal(
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
    logger.info(
        "Analyze request: filename=%s size=%d bytes",
        filename,
        len(contents),
    )

    from services.ingestion.converter import stage_extract

    try:
        ir_doc = await asyncio.to_thread(stage_extract, contents, filename)
    except Exception as exc:
        logger.exception("Extraction failed for %s", filename)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF extraction failed: {exc}",
        ) from exc

    proposals = _analyze_ir_document(ir_doc)

    # Build summary counts
    severity_counts: dict[str, int] = {"critical": 0, "serious": 0, "moderate": 0}
    auto_fixable_count = 0
    needs_review_count = 0
    for p in proposals:
        if p.severity in severity_counts:
            severity_counts[p.severity] += 1
        if p.auto_fixable:
            auto_fixable_count += 1
        else:
            needs_review_count += 1

    task_id = str(uuid.uuid4())

    return AnalysisResult(
        task_id=task_id,
        filename=filename,
        page_count=ir_doc.page_count,
        proposals=proposals,
        summary=AnalysisSummary(
            total_issues=len(proposals),
            critical=severity_counts["critical"],
            serious=severity_counts["serious"],
            moderate=severity_counts["moderate"],
            auto_fixable=auto_fixable_count,
            needs_review=needs_review_count,
        ),
    )


# ---------------------------------------------------------------------------
# Remediate endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/remediate",
    summary="Apply approved remediations and return accessible document",
    description=(
        "Accepts a PDF file and a list of approved proposal IDs, runs the "
        "full remediation pipeline, and returns the accessible output. "
        "For the POC, all remediations are applied regardless of the "
        "approved_ids list (selective application requires a major refactor)."
    ),
)
async def remediate_document(
    file: UploadFile = File(..., description="PDF file to remediate"),
    output_format: Literal["html", "pdf"] = Query(
        default="html", description="Output format: html or pdf"
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
    stem = Path(filename).stem
    logger.info(
        "Remediate request: filename=%s size=%d bytes format=%s",
        filename,
        len(contents),
        output_format,
    )

    from services.ingestion.converter import convert_pdf_sync

    try:
        output_bytes, content_type, task_id = await asyncio.to_thread(
            convert_pdf_sync,
            contents,
            filename,
            output_format,
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
        logger.exception("Remediation failed for %s", filename)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Remediation failed: {exc}",
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
