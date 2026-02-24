"""FastAPI router for rules ledger management."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from services.common.auth import require_admin, require_reviewer
from services.common.config import settings
from services.common.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RuleCreateRequest(BaseModel):
    trigger_pattern: str = Field(description="Pattern like 'table:missing_headers'")
    action: dict = Field(description="Action dict like {'type': 'add_scope', 'value': 'col'}")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_from: Optional[str] = None


class RuleStatusUpdate(BaseModel):
    status: str = Field(description="'candidate', 'active', or 'retired'")


# GET /api/rules — list all rules (active by default)
@router.get("", response_model=list[dict])
async def list_rules(
    rule_status: Optional[str] = Query(default=None, alias="status"),
    user: dict = Depends(require_reviewer),
):
    db = get_db(settings.db_path)
    if rule_status == "active" or rule_status is None:
        return db.get_active_rules()
    # For other statuses, query all and filter via internal helper
    return db._all(
        "SELECT * FROM rules_ledger WHERE status=? ORDER BY confidence DESC",
        (rule_status,),
        table="rules_ledger",
    )


# POST /api/rules — create a new rule (admin only)
@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: RuleCreateRequest,
    user: dict = Depends(require_admin),
):
    db = get_db(settings.db_path)
    rule_id = str(uuid.uuid4())
    result = db.insert_rule(
        rule_id=rule_id,
        trigger_pattern=body.trigger_pattern,
        action=body.action,
        confidence=body.confidence,
        created_from=body.created_from,
    )
    db.log_audit("rule", rule_id, "create", performed_by=user["user_id"])
    return result


# PATCH /api/rules/{id}/status — update rule status (admin only)
@router.patch("/{rule_id}/status", response_model=dict)
async def update_rule_status(
    rule_id: str,
    body: RuleStatusUpdate,
    user: dict = Depends(require_admin),
):
    db = get_db(settings.db_path)

    if body.status not in ("candidate", "active", "retired"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{body.status}'. Must be 'candidate', 'active', or 'retired'.",
        )

    result = db.update_rule_status(rule_id, body.status)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")

    db.log_audit(
        "rule",
        rule_id,
        "status_update",
        performed_by=user["user_id"],
        new_value=body.status,
    )
    return result


# POST /api/rules/{id}/validate — record a document validation for a rule
@router.post("/{rule_id}/validate", response_model=dict)
async def validate_rule_on_doc(
    rule_id: str,
    document_id: str = Query(..., description="Document ID where rule was validated"),
    user: dict = Depends(require_reviewer),
):
    db = get_db(settings.db_path)
    result = db.add_validated_doc(rule_id, document_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    return result
