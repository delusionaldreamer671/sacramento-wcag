"""FastAPI router for HITL review-item endpoints.

Exposes three endpoints consumed by the HITL dashboard:

  GET  /api/documents/{documentId}/review-items      — fetchReviewItems()
  POST /api/review-items/{itemId}/decision            — submitReview()
  POST /api/review-items/batch-approve               — batchApprove()

Authentication is intentionally omitted for the POC: the sync convert
flow does not attach auth tokens and the dashboard does not yet send them.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from services.common.config import settings
from services.common.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["review-items"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ReviewDecisionRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    reviewer_edit: Optional[str] = Field(default=None)
    reviewed_by: Optional[str] = Field(default=None)


class BatchApproveRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1)
    reviewed_by: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# GET /api/documents/{documentId}/review-items
# ---------------------------------------------------------------------------


@router.get(
    "/documents/{document_id}/review-items",
    response_model=list[dict],
    summary="List review items for a document",
)
async def get_document_review_items(document_id: str) -> list[dict]:
    """Return all HITL review items for the given document.

    Pending items (reviewer_decision is null) are returned first so the
    dashboard can render unreviewed work at the top of the queue.
    """
    db = get_db(settings.db_path)

    doc = db.get_document(document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    items: list[dict] = db.get_review_items(document_id)

    # Sort: pending (None decision) first, then reviewed items
    items.sort(key=lambda item: (item.get("reviewer_decision") is not None,))

    logger.info(
        "Fetched %d review items for document %s", len(items), document_id
    )
    return items


# ---------------------------------------------------------------------------
# POST /api/review-items/batch-approve  — MUST be registered before /{itemId}
# ---------------------------------------------------------------------------


@router.post(
    "/review-items/batch-approve",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Batch-approve multiple SIMPLE-flagged review items",
)
async def batch_approve_items(body: BatchApproveRequest) -> None:
    """Approve all listed review items in a single request.

    Designed for SIMPLE-complexity items where the AI suggestion can be
    accepted without individual inspection. Returns 204 on success.
    """
    db = get_db(settings.db_path)

    not_found: list[str] = []
    for item_id in body.item_ids:
        result = db.update_review_decision(
            item_id=item_id,
            decision="approve",
            edit=None,
            reviewer_id=body.reviewed_by,
        )
        if result is None:
            not_found.append(item_id)

    if not_found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review items not found: {not_found}",
        )

    logger.info(
        "Batch-approved %d review items (reviewer=%s)",
        len(body.item_ids),
        body.reviewed_by,
    )


# ---------------------------------------------------------------------------
# POST /api/review-items/{itemId}/decision
# ---------------------------------------------------------------------------


@router.post(
    "/review-items/{item_id}/decision",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Submit a reviewer decision for a single review item",
)
async def submit_review_decision(
    item_id: str,
    body: ReviewDecisionRequest,
) -> None:
    """Record a reviewer's approve / edit / reject decision.

    - **approve**: accept the AI suggestion as-is.
    - **edit**: accept a modified version supplied in ``reviewer_edit``.
    - **reject**: flag the item for manual remediation.

    Returns 204 No Content on success.
    """
    db = get_db(settings.db_path)

    result = db.update_review_decision(
        item_id=item_id,
        decision=body.decision,
        edit=body.reviewer_edit,
        reviewer_id=body.reviewed_by,
    )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review item '{item_id}' not found.",
        )

    logger.info(
        "Review decision '%s' recorded for item %s (reviewer=%s)",
        body.decision,
        item_id,
        body.reviewed_by,
    )
