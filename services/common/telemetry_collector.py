"""Pipeline telemetry collector for per-document processing metrics.

Collects timing, extraction, AI, validation, and output metrics during a
single pipeline run and persists them to the ``pipeline_telemetry`` SQLite
table.  Designed to be fail-safe: if persistence fails, the pipeline
continues unaffected.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class TelemetryCollector:
    """Collects metrics during a single pipeline run and persists them."""

    def __init__(
        self,
        document_id: str,
        task_id: str,
        filename: str,
        file_size_bytes: int = 0,
    ) -> None:
        self.record: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "document_id": document_id,
            "task_id": task_id,
            "filename": filename,
            "file_size_bytes": file_size_bytes,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running",
        }
        self._stage_start: float | None = None
        self._stage_name: str | None = None

    def start_stage(self, stage: str) -> None:
        """Mark the start of a pipeline stage for duration measurement."""
        self._stage_start = time.monotonic()
        self._stage_name = stage

    def end_stage(self) -> None:
        """Mark the end of the current stage, recording duration."""
        if self._stage_start and self._stage_name:
            duration = time.monotonic() - self._stage_start
            key = f"{self._stage_name}_duration_s"
            self.record[key] = round(duration, 3)
            self._stage_start = None
            self._stage_name = None

    def set(self, key: str, value: Any) -> None:
        """Set a single metric value."""
        self.record[key] = value

    def increment(self, key: str, amount: int = 1) -> None:
        """Increment a counter metric."""
        self.record[key] = self.record.get(key, 0) + amount

    def mark_success(self) -> None:
        """Mark pipeline as successful."""
        self.record["status"] = "success"
        self.record["completed_at"] = datetime.now(timezone.utc).isoformat()
        started = datetime.fromisoformat(self.record["started_at"])
        completed = datetime.fromisoformat(self.record["completed_at"])
        self.record["total_duration_s"] = round(
            (completed - started).total_seconds(), 3
        )

    def mark_failed(self, error_message: str, error_stage: str = "") -> None:
        """Mark pipeline as failed with error details."""
        self.record["status"] = "failed"
        self.record["completed_at"] = datetime.now(timezone.utc).isoformat()
        self.record["error_message"] = str(error_message)[:2000]  # Truncate
        self.record["error_stage"] = error_stage
        started = datetime.fromisoformat(self.record["started_at"])
        completed = datetime.fromisoformat(self.record["completed_at"])
        self.record["total_duration_s"] = round(
            (completed - started).total_seconds(), 3
        )

    def persist(self, db: Any) -> None:
        """Write the telemetry record to the database.

        Fail-safe: any exception is logged but never propagated so the
        pipeline is not disrupted by telemetry failures.
        """
        try:
            db.insert_telemetry(self.record)
        except Exception:
            # Log at ERROR so the audit trail is captured in Cloud Run logs
            # even when DB persistence fails.  Truncate record to 2000 chars
            # to avoid flooding the log stream.
            import json as _json
            try:
                record_repr = _json.dumps(self.record)[:2000]
            except Exception:
                record_repr = str(self.record)[:2000]
            logger.error(
                "TelemetryCollector.persist failed for task_id=%s — "
                "telemetry record lost from DB but captured here: %s",
                self.record.get("task_id", "?"),
                record_repr,
                exc_info=True,
            )
