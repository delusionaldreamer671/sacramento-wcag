"""Add gcs_path column to image_assets for hybrid GCS storage.

When gcs_path is set, images are served from GCS (signed URL).
When NULL, images are served from the BLOB column (backward compatible).

Revision ID: 002
Revises: 001
Create Date: 2026-02-26
"""
from __future__ import annotations

import logging

from alembic import op

logger = logging.getLogger(__name__)

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    # SQLite doesn't support IF NOT EXISTS for ALTER TABLE ADD COLUMN,
    # so we catch the specific "duplicate column" error and ignore it.
    # All other errors are re-raised so migration failures are visible.
    try:
        op.execute(
            "ALTER TABLE image_assets ADD COLUMN gcs_path TEXT DEFAULT NULL"
        )
    except Exception as exc:
        exc_str = str(exc).lower()
        if "duplicate column" in exc_str or "already exists" in exc_str:
            # Column already exists — safe to continue
            logger.info(
                "Migration 002: gcs_path column already exists in image_assets — skipping ADD COLUMN"
            )
        else:
            logger.warning(
                "Migration 002: unexpected error adding gcs_path column: %s", exc
            )
            raise


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE image_assets DROP COLUMN IF EXISTS gcs_path")
    # SQLite doesn't support DROP COLUMN in older versions;
    # the column is nullable so leaving it is safe.
