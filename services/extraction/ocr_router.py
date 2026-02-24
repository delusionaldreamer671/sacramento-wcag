"""OCR decisioning layer — per-page profiling and routing.

Analyzes each PDF page to determine whether it has extractable text
or needs OCR via GCP Document AI. Produces a doc_profile.json with
per-page routing decisions.

Pages with <20 characters are classified as needing OCR.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from services.common.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PageProfile(BaseModel):
    """Per-page OCR routing decision."""

    page_num: int
    has_extractable_text: bool
    text_coverage_ratio: float = 1.0
    image_coverage_ratio: float = 0.0
    route: Literal["adobe", "ocr"] = "adobe"
    char_count: int = 0


class DocumentProfile(BaseModel):
    """Document-level OCR routing summary."""

    filename: str
    page_count: int
    pages: list[PageProfile] = Field(default_factory=list)
    overall_route: Literal["all_adobe", "all_ocr", "mixed"] = "all_adobe"


# ---------------------------------------------------------------------------
# Profiling functions
# ---------------------------------------------------------------------------


def profile_page(pdf_bytes: bytes, page_num: int) -> PageProfile:
    """Analyze a single page for text extractability.

    Uses pypdf to extract text. Pages with fewer characters than the
    threshold (default 20) are classified as needing OCR.
    """
    try:
        from pypdf import PdfReader
        import io

        reader = PdfReader(io.BytesIO(pdf_bytes))
        if page_num >= len(reader.pages):
            return PageProfile(
                page_num=page_num,
                has_extractable_text=False,
                text_coverage_ratio=0.0,
                image_coverage_ratio=1.0,
                route="ocr",
                char_count=0,
            )

        page = reader.pages[page_num]
        text = page.extract_text() or ""
        char_count = len(text.strip())

        has_text = char_count >= settings.ocr_min_chars_threshold
        text_coverage = min(1.0, char_count / 2000.0)
        image_coverage = 1.0 - text_coverage
        route: Literal["adobe", "ocr"] = "adobe" if has_text else "ocr"

        return PageProfile(
            page_num=page_num,
            has_extractable_text=has_text,
            text_coverage_ratio=round(text_coverage, 3),
            image_coverage_ratio=round(image_coverage, 3),
            route=route,
            char_count=char_count,
        )

    except Exception as exc:
        logger.warning("Failed to profile page %d: %s", page_num, exc)
        return PageProfile(
            page_num=page_num,
            has_extractable_text=True,
            route="adobe",
        )


def profile_document(pdf_bytes: bytes, filename: str) -> DocumentProfile:
    """Profile all pages in a PDF for OCR routing decisions."""
    try:
        from pypdf import PdfReader
        import io

        reader = PdfReader(io.BytesIO(pdf_bytes))
        page_count = len(reader.pages)
    except Exception as exc:
        logger.warning("Failed to read PDF for profiling: %s", exc)
        return DocumentProfile(
            filename=filename,
            page_count=0,
            overall_route="all_adobe",
        )

    pages: list[PageProfile] = []
    for i in range(page_count):
        pages.append(profile_page(pdf_bytes, i))

    ocr_count = sum(1 for p in pages if p.route == "ocr")
    adobe_count = sum(1 for p in pages if p.route == "adobe")

    if ocr_count == 0:
        overall = "all_adobe"
    elif adobe_count == 0:
        overall = "all_ocr"
    else:
        overall = "mixed"

    profile = DocumentProfile(
        filename=filename,
        page_count=page_count,
        pages=pages,
        overall_route=overall,
    )

    logger.info(
        "Document profile: %s — %d pages, %d adobe / %d ocr → %s",
        filename, page_count, adobe_count, ocr_count, overall,
    )
    return profile
