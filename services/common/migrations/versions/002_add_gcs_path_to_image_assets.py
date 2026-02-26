"""Add gcs_path column to image_assets for hybrid GCS storage.

When gcs_path is set, images are served from GCS (signed URL).
When NULL, images are served from the BLOB column (backward compatible).

Revision ID: 002
Revises: 001
Create Date: 2026-02-26
"""
from __future__ import annotations

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    # SQLite doesn't support IF NOT EXISTS for ALTER TABLE ADD COLUMN,
    # so we catch the error if the column already exists.
    try:
        op.execute(
            "ALTER TABLE image_assets ADD COLUMN gcs_path TEXT DEFAULT NULL"
        )
    except Exception:
        # Column already exists — safe to continue
        pass


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE image_assets DROP COLUMN IF EXISTS gcs_path")
    # SQLite doesn't support DROP COLUMN in older versions;
    # the column is nullable so leaving it is safe.
