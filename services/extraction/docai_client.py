"""GCP Document AI OCR client.

Wraps the Document AI API to perform OCR on scanned/image PDF pages
and convert results to IR blocks.

Setup:
    1. Enable Document AI API: gcloud services enable documentai.googleapis.com
    2. Add documentai.apiUser role to service account
    3. Create a Document OCR processor in GCP Console
    4. Set env vars:
       WCAG_DOCAI_PROCESSOR_ID=<processor-id>
       WCAG_DOCAI_LOCATION=us
"""

from __future__ import annotations

import logging
import time
from typing import Any

from services.common.config import settings
from services.common.ir import (
    BlockSource,
    BlockType,
    BoundingBox,
    IRBlock,
    RemediationStatus,
)

logger = logging.getLogger(__name__)

_DOCAI_AVAILABLE = False

try:
    from google.cloud import documentai_v1 as documentai
    _DOCAI_AVAILABLE = True
except ImportError:
    documentai = None  # type: ignore[assignment]


def is_available() -> bool:
    """Check if Document AI is configured and available."""
    return (
        _DOCAI_AVAILABLE
        and bool(settings.docai_processor_id)
        and bool(settings.gcp_project_id)
    )


class DocumentAIClient:
    """GCP Document AI OCR processor wrapper."""

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        processor_id: str | None = None,
    ) -> None:
        if not _DOCAI_AVAILABLE:
            raise RuntimeError(
                "google-cloud-documentai not installed. "
                "Run: pip install google-cloud-documentai"
            )

        self._project_id = project_id or settings.gcp_project_id
        self._location = location or settings.docai_location
        self._processor_id = processor_id or settings.docai_processor_id

        if not self._processor_id:
            raise ValueError(
                "Document AI processor ID not configured. "
                "Set WCAG_DOCAI_PROCESSOR_ID environment variable."
            )

        opts = {}
        api_endpoint = f"{self._location}-documentai.googleapis.com"
        opts["api_endpoint"] = api_endpoint
        self._client = documentai.DocumentProcessorServiceClient(
            client_options=opts,
        )
        self._processor_name = self._client.processor_path(
            self._project_id, self._location, self._processor_id,
        )

        logger.info(
            "DocumentAIClient initialized: project=%s location=%s processor=%s",
            self._project_id, self._location, self._processor_id,
        )

    def ocr_pdf(self, pdf_bytes: bytes) -> list[IRBlock]:
        """Run OCR on a full PDF and return IR blocks.

        Sends the entire PDF to Document AI. Returns blocks for all pages.
        """
        raw_document = documentai.RawDocument(
            content=pdf_bytes,
            mime_type="application/pdf",
        )

        request = documentai.ProcessRequest(
            name=self._processor_name,
            raw_document=raw_document,
        )

        logger.info("Sending %d bytes to Document AI OCR", len(pdf_bytes))

        # CRITICAL-4.4: Add timeout and retry for transient 503 errors.
        max_retries = 2
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 2):  # 1 initial + 2 retries
            try:
                result = self._client.process_document(
                    request=request, timeout=180
                )
                blocks = self._convert_to_ir_blocks(result.document)
                logger.info("Document AI returned %d blocks", len(blocks))
                return blocks
            except Exception as exc:
                # Only retry on ServiceUnavailable (503) — transient error
                exc_type_name = type(exc).__qualname__
                is_503 = (
                    "ServiceUnavailable" in exc_type_name
                    or "503" in str(exc)
                )
                if is_503 and attempt <= max_retries:
                    last_exc = exc
                    wait = 2.0 ** attempt
                    logger.warning(
                        "Document AI attempt %d/%d failed (503). "
                        "Retrying in %.1fs. Error: %s",
                        attempt, max_retries + 1, wait, exc,
                    )
                    time.sleep(wait)
                else:
                    raise

        # Should not reach here, but satisfy type checker
        assert last_exc is not None
        raise last_exc

    def ocr_page(self, pdf_bytes: bytes, page_num: int) -> list[IRBlock]:
        """Run OCR and return blocks for a specific page only.

        Sends the full PDF but filters results to the requested page.
        """
        all_blocks = self.ocr_pdf(pdf_bytes)
        return [b for b in all_blocks if b.page_num == page_num]

    def _convert_to_ir_blocks(
        self, document: Any,
    ) -> list[IRBlock]:
        """Convert Document AI output to IR blocks."""
        blocks: list[IRBlock] = []

        for page_idx, page in enumerate(document.pages):
            # Process paragraphs
            for paragraph in page.paragraphs:
                text = self._get_text(paragraph.layout, document.text)
                if not text.strip():
                    continue

                confidence = paragraph.layout.confidence
                bbox = self._get_bbox(paragraph.layout)

                blocks.append(IRBlock(
                    block_type=BlockType.PARAGRAPH,
                    content=text.strip(),
                    bbox=bbox,
                    page_num=page_idx,
                    confidence=confidence,
                    source=BlockSource.OCR,
                    remediation_status=RemediationStatus.RAW,
                ))

            # Process tables
            for table in page.tables:
                table_block = self._convert_table(table, document.text, page_idx)
                if table_block:
                    blocks.append(table_block)

        return blocks

    def _convert_table(
        self, table: Any, full_text: str, page_num: int,
    ) -> IRBlock | None:
        """Convert a Document AI table to an IR table block."""
        headers: list[str] = []
        rows: list[list[str]] = []

        # Header rows
        for header_row in table.header_rows:
            row_texts = []
            for cell in header_row.cells:
                text = self._get_text(cell.layout, full_text)
                row_texts.append(text.strip())
            if row_texts:
                headers = row_texts

        # Body rows
        for body_row in table.body_rows:
            row_texts = []
            for cell in body_row.cells:
                text = self._get_text(cell.layout, full_text)
                row_texts.append(text.strip())
            if row_texts:
                rows.append(row_texts)

        if not headers and not rows:
            return None

        confidence = table.layout.confidence if hasattr(table, "layout") else 0.8

        return IRBlock(
            block_type=BlockType.TABLE,
            content="",
            page_num=page_num,
            confidence=confidence,
            source=BlockSource.OCR,
            remediation_status=RemediationStatus.RAW,
            attributes={"headers": headers, "rows": rows},
        )

    @staticmethod
    def _get_text(layout: Any, full_text: str) -> str:
        """Extract text from a Document AI layout element."""
        text = ""
        for segment in layout.text_anchor.text_segments:
            start = int(segment.start_index) if segment.start_index else 0
            end = int(segment.end_index)
            text += full_text[start:end]
        return text

    @staticmethod
    def _get_bbox(layout: Any) -> BoundingBox:
        """Extract bounding box from a Document AI layout element."""
        vertices = layout.bounding_poly.normalized_vertices
        if len(vertices) >= 4:
            return BoundingBox(
                x1=vertices[0].x,
                y1=vertices[0].y,
                x2=vertices[2].x,
                y2=vertices[2].y,
            )
        return BoundingBox()
