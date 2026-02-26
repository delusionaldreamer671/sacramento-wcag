"""API endpoint for retrieving remediation audit trail."""
from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException

from services.common.constants import API_V1_PREFIX

router = APIRouter(prefix=API_V1_PREFIX, tags=["fixes"])

# CRITICAL-1.2: Thread-safe in-memory cache for sync conversions.
#
# The original implementation used a plain dict without any locking.  Under
# concurrent requests (multiple workers or asyncio tasks calling cache_events
# simultaneously) this could produce a KeyError or corrupt the dict internals
# during the "evict oldest" operation, which reads and deletes while another
# thread may be inserting.
#
# Fix: guard all cache reads and writes with a threading.Lock.  The lock is
# reentrant-safe (plain Lock is sufficient here because no single call path
# acquires it twice).
_SYNC_EVENT_CACHE: dict[str, list[dict]] = {}
_SYNC_EVENT_CACHE_LOCK = threading.Lock()
_SYNC_EVENT_CACHE_MAX = 100


def cache_events(task_id: str, events: list[dict]) -> None:
    """Store events from a sync conversion for later retrieval (thread-safe)."""
    with _SYNC_EVENT_CACHE_LOCK:
        _SYNC_EVENT_CACHE[task_id] = events
        # Keep cache bounded — evict oldest entry if over the limit
        if len(_SYNC_EVENT_CACHE) > _SYNC_EVENT_CACHE_MAX:
            oldest_key = next(iter(_SYNC_EVENT_CACHE))
            del _SYNC_EVENT_CACHE[oldest_key]


@router.get("/{task_id}/fixes-applied")
async def get_fixes_applied(task_id: str) -> dict:
    """Retrieve the remediation audit trail for a conversion task."""
    # Try in-memory cache first (sync conversions).
    # CRITICAL-1.2: acquire lock before reading to prevent KeyError or partial
    # reads under concurrent write operations.
    with _SYNC_EVENT_CACHE_LOCK:
        cached = _SYNC_EVENT_CACHE.get(task_id)

    if cached is not None:
        return {
            "task_id": task_id,
            "event_count": len(cached),
            "events": cached,
        }

    # Try SQLite (async conversions).
    # HIGH-1.7: always pass settings.db_path to get_db() so it connects to the
    # correct database file, not whatever the singleton was last initialised with.
    try:
        from services.common.config import settings
        from services.common.database import get_db
        db = get_db(settings.db_path)
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
