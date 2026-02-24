"""FastAPI router for PDF ingestion endpoints.

Handles document upload, status retrieval, and listing.
Document metadata is persisted to SQLite via the database module.
"""

from __future__ import annotations

import asyncio
import io
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response

from services.common import gcs_client, pubsub_client
from services.common.config import settings
from services.common.database import get_db
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

    if output_format == "zip":
        # Generate both HTML and PDF, then bundle them into a ZIP archive.
        try:
            html_bytes, _ = await asyncio.to_thread(
                convert_pdf_sync,
                contents,
                filename,
                "html",
            )
            pdf_bytes, _ = await asyncio.to_thread(
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
            output_bytes, content_type = await asyncio.to_thread(
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
        headers={"Content-Disposition": disposition},
    )
