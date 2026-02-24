"""Intermediate Representation (IR) for the WCAG remediation pipeline.

The IR is the canonical data format that ALL pipeline stages produce and consume.
Every stage reads an IRDocument in and writes an IRDocument out.

Schema: IRDocument → IRPage[] → IRBlock[]
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _new_block_id() -> str:
    return str(uuid.uuid4())


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    IMAGE = "image"
    LIST = "list"
    FORM_FIELD = "form_field"


class BlockSource(str, Enum):
    ADOBE = "adobe"
    OCR = "ocr"
    MANUAL_OVERRIDE = "manual_override"
    PYPDF_FALLBACK = "pypdf_fallback"


class RemediationStatus(str, Enum):
    RAW = "raw"
    AI_DRAFTED = "ai_drafted"
    HUMAN_REVIEWED = "human_reviewed"
    APPROVED = "approved"
    FLAGGED = "flagged"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class BoundingBox(BaseModel):
    """Coordinate box for an element on a page (in PDF points)."""

    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0


class IRBlock(BaseModel):
    """A single structural element extracted from a PDF page.

    The ``attributes`` dict carries type-specific data:
    - heading: {"level": 1}
    - table:   {"headers": [...], "rows": [[...], ...]}
    - image:   {"alt": "...", "src": "..."}
    - list:    {"items": [...]}
    """

    block_id: str = Field(default_factory=_new_block_id)
    block_type: BlockType
    content: str = ""
    bbox: BoundingBox = Field(default_factory=BoundingBox)
    page_num: int = 0
    confidence: float = 1.0
    source: BlockSource = BlockSource.ADOBE
    wcag_criteria: list[str] = Field(default_factory=list)
    remediation_status: RemediationStatus = RemediationStatus.RAW
    attributes: dict[str, Any] = Field(default_factory=dict)


class IRPage(BaseModel):
    """One page of a document, containing ordered blocks."""

    page_num: int
    width: float = 612.0   # Default US Letter width in points
    height: float = 792.0  # Default US Letter height in points
    blocks: list[IRBlock] = Field(default_factory=list)

    # Page-level profiling (populated by OCR router)
    has_extractable_text: bool = True
    text_coverage_ratio: float = 1.0
    image_coverage_ratio: float = 0.0
    extraction_method: str = "adobe"  # "adobe" | "ocr" | "pypdf"


class IRDocument(BaseModel):
    """Top-level intermediate representation for a PDF document.

    This is the single interchange format between all pipeline stages.
    """

    document_id: str
    filename: str
    page_count: int = 0
    pages: list[IRPage] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utcnow_iso)
    language: str = "en"
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ---- Convenience helpers ----

    def all_blocks(self) -> list[IRBlock]:
        """Flatten all blocks across all pages in reading order."""
        return [block for page in self.pages for block in page.blocks]

    def blocks_by_type(self, block_type: BlockType) -> list[IRBlock]:
        """Return all blocks matching a specific type."""
        return [b for b in self.all_blocks() if b.block_type == block_type]

    def to_legacy_elements(self) -> list[dict[str, Any]]:
        """Convert IR blocks to the legacy list[dict] format for PDFUABuilder.

        Each block becomes: {"type": ..., "content": ..., "attributes": {...}}
        This is the backward-compatibility bridge so the existing PDFUABuilder
        and its add_element() API continue to work without changes.
        """
        elements: list[dict[str, Any]] = []
        for block in self.all_blocks():
            elements.append({
                "type": block.block_type.value,
                "content": block.content,
                "attributes": dict(block.attributes),
            })
        return elements
