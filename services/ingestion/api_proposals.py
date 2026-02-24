"""FastAPI router for change proposals."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from services.common.auth import require_admin, require_reviewer
from services.common.change_evaluator import evaluate_proposal
from services.common.config import settings
from services.common.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


# Request/response models
class ProposalCreateRequest(BaseModel):
    document_id: str
    review_item_id: Optional[str] = None
    human_comment: str
    element_type: str = "paragraph"
    finding_severity: Optional[str] = None
    finding_criterion: Optional[str] = None


class ProposalResponse(BaseModel):
    id: str
    document_id: str
    review_item_id: Optional[str]
    proposed_by: str
    human_comment: str
    system_evaluation: dict | str
    system_recommendation: str
    human_override: int
    status: str
    created_at: str
    resolved_at: Optional[str]
    resolved_by: Optional[str]


# POST /api/proposals — create a new change proposal
@router.post("", response_model=ProposalResponse, status_code=status.HTTP_201_CREATED)
async def create_proposal(
    body: ProposalCreateRequest,
    user: dict = Depends(require_reviewer),
):
    db = get_db(settings.db_path)

    # Verify document exists
    doc = db.get_document(body.document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document '{body.document_id}' not found.")

    # Run deterministic evaluation
    evaluation = evaluate_proposal(
        human_comment=body.human_comment,
        element_type=body.element_type,
        finding_severity=body.finding_severity,
        finding_criterion=body.finding_criterion,
    )

    proposal_id = str(uuid.uuid4())
    result = db.insert_proposal(
        proposal_id=proposal_id,
        document_id=body.document_id,
        proposed_by=user["user_id"],
        human_comment=body.human_comment,
        system_evaluation=evaluation,
        system_recommendation=evaluation["recommendation"],
        review_item_id=body.review_item_id,
    )

    return result


# GET /api/proposals — list proposals with optional filters
@router.get("", response_model=list[dict])
async def list_proposals(
    document_id: Optional[str] = Query(default=None),
    proposal_status: Optional[str] = Query(default=None, alias="status"),
    user: dict = Depends(require_reviewer),
):
    db = get_db(settings.db_path)
    return db.list_proposals(document_id=document_id, status=proposal_status)


# GET /api/proposals/{id} — get a single proposal
@router.get("/{proposal_id}", response_model=dict)
async def get_proposal_detail(
    proposal_id: str,
    user: dict = Depends(require_reviewer),
):
    db = get_db(settings.db_path)
    proposal = db.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found.")
    return proposal


# POST /api/proposals/{id}/apply — apply an approved proposal
@router.post("/{proposal_id}/apply", response_model=dict)
async def apply_proposal_endpoint(
    proposal_id: str,
    user: dict = Depends(require_reviewer),
):
    db = get_db(settings.db_path)
    proposal = db.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found.")

    if proposal["status"] not in ("pending", "approved"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot apply proposal in '{proposal['status']}' status.",
        )

    # Apply the review decision if there's a linked review item
    if proposal.get("review_item_id"):
        db.update_review_decision(
            item_id=proposal["review_item_id"],
            decision="edit",
            edit=proposal["human_comment"],
            reviewer_id=user["user_id"],
        )

    updated = db.update_proposal_status(
        proposal_id, "applied", resolved_by=user["user_id"]
    )
    return updated


# POST /api/proposals/{id}/rollback — rollback an applied proposal
@router.post("/{proposal_id}/rollback", response_model=dict)
async def rollback_proposal_endpoint(
    proposal_id: str,
    user: dict = Depends(require_admin),
):
    db = get_db(settings.db_path)
    proposal = db.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found.")

    if proposal["status"] != "applied":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot rollback proposal in '{proposal['status']}' status. Only 'applied' proposals can be rolled back.",
        )

    # Reset the review item if linked
    if proposal.get("review_item_id"):
        db.update_review_decision(
            item_id=proposal["review_item_id"],
            decision=None,
            edit=None,
            reviewer_id=user["user_id"],
        )

    updated = db.update_proposal_status(
        proposal_id, "rolled_back", resolved_by=user["user_id"]
    )
    return updated
