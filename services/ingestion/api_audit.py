"""FastAPI router for audit trail queries."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from services.common.auth import require_reviewer
from services.common.config import settings
from services.common.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])


# GET /api/audit/{entity_type}/{entity_id} — get audit log for an entity
@router.get("/{entity_type}/{entity_id}", response_model=list[dict])
async def get_audit_trail(
    entity_type: str,
    entity_id: str,
    user: dict = Depends(require_reviewer),
):
    db = get_db(settings.db_path)
    return db.get_audit_log(entity_type, entity_id)
