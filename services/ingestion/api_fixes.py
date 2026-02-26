"""API endpoint for retrieving remediation audit trail."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.common.constants import API_V1_PREFIX

router = APIRouter(prefix=API_V1_PREFIX, tags=["fixes"])

# In-memory cache for sync conversions (populated by convert_pdf_sync)
_SYNC_EVENT_CACHE: dict[str, list[dict]] = {}


def cache_events(task_id: str, events: list[dict]) -> None:
    """Store events from a sync conversion for later retrieval."""
    _SYNC_EVENT_CACHE[task_id] = events
    # Keep cache bounded — evict oldest if > 100 entries
    if len(_SYNC_EVENT_CACHE) > 100:
        oldest_key = next(iter(_SYNC_EVENT_CACHE))
        del _SYNC_EVENT_CACHE[oldest_key]


@router.get("/{task_id}/fixes-applied")
async def get_fixes_applied(task_id: str) -> dict:
    """Retrieve the remediation audit trail for a conversion task."""
    # Try in-memory cache first (sync conversions)
    if task_id in _SYNC_EVENT_CACHE:
        events = _SYNC_EVENT_CACHE[task_id]
        return {
            "task_id": task_id,
            "event_count": len(events),
            "events": events,
        }

    # Try SQLite (async conversions)
    try:
        from services.common.database import get_db
        db = get_db()
        events = db.get_remediation_events(task_id)
        if events:
            return {
                "task_id": task_id,
                "event_count": len(events),
                "events": events,
            }
    except Exception:
        pass

    raise HTTPException(status_code=404, detail=f"No remediation events found for task {task_id}")
