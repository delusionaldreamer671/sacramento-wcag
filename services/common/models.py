"""Pydantic models for the WCAG PDF Remediation Pipeline.

Defines all shared data models used across ingestion, extraction,
AI drafting, HITL review, and recompilation services.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class DocumentStatus(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    AI_DRAFTING = "ai_drafting"
    HITL_REVIEW = "hitl_review"
    APPROVED = "approved"
    RECOMPILING = "recompiling"
    COMPLETE = "complete"
    FAILED = "failed"


class WCAGCriterion(str, Enum):
    ALT_TEXT = "1.1.1"
    INFO_RELATIONSHIPS = "1.3.1"
    READING_ORDER = "1.3.2"
    SENSORY = "1.3.3"
    COLOR_CONTRAST = "1.4.3"
    IMAGES_OF_TEXT = "1.4.5"
    HEADINGS_LABELS = "2.4.6"
    LINK_PURPOSE = "2.4.4"
    LANGUAGE = "3.1.1"
    NAME_ROLE_VALUE = "4.1.2"


class ComplexityFlag(str, Enum):
    SIMPLE = "simple"
    REVIEW = "review"
    MANUAL = "manual"


class PDFDocument(BaseModel):
    id: str = Field(default_factory=_new_id, description="UUID for the document")
    filename: str
    gcs_input_path: str
    gcs_output_path: Optional[str] = None
    status: DocumentStatus = DocumentStatus.QUEUED
    page_count: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ExtractionResult(BaseModel):
    document_id: str
    adobe_job_id: str
    extracted_json_path: str
    auto_tag_json_path: str
    elements_count: int = 0
    images_count: int = 0
    tables_count: int = 0


class WCAGFinding(BaseModel):
    id: str = Field(default_factory=_new_id)
    document_id: str
    element_id: str
    criterion: WCAGCriterion
    severity: str = Field(description="critical, serious, moderate, or minor")
    description: str
    suggested_fix: Optional[str] = None
    ai_draft: Optional[str] = None
    complexity: ComplexityFlag = ComplexityFlag.SIMPLE


class HITLReviewItem(BaseModel):
    id: str = Field(default_factory=_new_id)
    document_id: str
    finding_id: str
    element_type: str
    original_content: dict
    ai_suggestion: str
    reviewer_decision: Optional[str] = None
    reviewer_edit: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None


class RemediatedDocument(BaseModel):
    document_id: str
    semantic_html_path: str
    pdfua_output_path: str
    axe_score: Optional[float] = None
    wcag_violations_remaining: int = 0
    manual_review_items: int = 0


# --- API Request/Response Models ---


class DocumentUploadResponse(BaseModel):
    document_id: str
    status: DocumentStatus
    message: str


class DocumentStatusResponse(BaseModel):
    document_id: str
    filename: str
    status: DocumentStatus
    page_count: int
    created_at: datetime
    updated_at: datetime


class ReviewDecision(BaseModel):
    decision: str = Field(description="approve, edit, or reject")
    reviewer_edit: Optional[str] = None
    reviewed_by: str


class BatchApproveRequest(BaseModel):
    item_ids: list[str]
    reviewed_by: str


class PipelineHealthResponse(BaseModel):
    status: str = "healthy"
    services: dict[str, str] = Field(default_factory=dict)
