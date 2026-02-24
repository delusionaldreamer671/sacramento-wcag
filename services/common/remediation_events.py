"""Remediation event tracking for the WCAG PDF pipeline.

Records every change made during remediation (alt text additions, heading
hierarchy fixes, table restructuring, etc.) as before/after diff events.
Used for audit trail and client reporting.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RemediationComponent(str, Enum):
    ALT_TEXT = "AltText"
    HEADING_HIERARCHY = "HeadingHierarchy"
    TABLE_STRUCTURE = "TableStructure"
    FIGURE_CAPTION = "FigureCaption"
    LANGUAGE_TAG = "LanguageTag"
    MARK_INFO = "MarkInfo"
    PDFUA_METADATA = "PDFUAMetadata"
    VIEWER_PREFERENCES = "ViewerPreferences"
    TAB_ORDER = "TabOrder"
    CIDSET_REMOVAL = "CIDSetRemoval"


class RemediationEvent(BaseModel):
    id: str = Field(default_factory=_new_id)
    document_id: str
    task_id: str
    component: RemediationComponent
    element_id: str = ""
    before: Any = None
    after: Any = None
    timestamp: str = Field(default_factory=_utcnow_iso)
    source: str = "pipeline"  # pipeline, ai, human, clause_fixer


class RemediationEventCollector:
    """In-memory collector for sync conversion path. Thread-safe per-request."""

    def __init__(self, document_id: str = "", task_id: str = "") -> None:
        self._events: list[RemediationEvent] = []
        self.document_id = document_id
        self.task_id = task_id

    def record(
        self,
        component: RemediationComponent,
        element_id: str = "",
        before: Any = None,
        after: Any = None,
        source: str = "pipeline",
    ) -> None:
        self._events.append(
            RemediationEvent(
                document_id=self.document_id,
                task_id=self.task_id,
                component=component,
                element_id=element_id,
                before=before,
                after=after,
                source=source,
            )
        )

    def events(self) -> list[RemediationEvent]:
        return list(self._events)

    def to_dict_list(self) -> list[dict]:
        return [e.model_dump(mode="json") for e in self._events]

    def persist_to_db(self, db: Any) -> None:
        """Write all events to the SQLite remediation_events table."""
        for event in self._events:
            db.insert_remediation_event(
                event_id=event.id,
                document_id=event.document_id,
                task_id=event.task_id,
                component=event.component.value,
                element_id=event.element_id,
                before=event.before,
                after=event.after,
                source=event.source,
                timestamp=event.timestamp,
            )
