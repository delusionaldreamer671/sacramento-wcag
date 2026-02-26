"""Add nvda_qa_results table for manual QA protocol.

Stores structured results from manual NVDA screen reader testing.
Each row represents one QA session for a document, containing a
JSON checklist of pass/fail results per check item.

Revision ID: 003
Revises: 002
Create Date: 2026-02-26
"""
from __future__ import annotations

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | None = None
depends_on: str | None = None


_CREATE_TABLE_SQLITE = """\
CREATE TABLE IF NOT EXISTS nvda_qa_results (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    task_id TEXT NOT NULL DEFAULT '',
    reviewer TEXT NOT NULL DEFAULT '',
    checklist_json TEXT NOT NULL DEFAULT '[]',
    overall_status TEXT NOT NULL DEFAULT 'pending',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT
)"""

_CREATE_TABLE_POSTGRES = _CREATE_TABLE_SQLITE  # Same DDL works for both


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    ddl = _CREATE_TABLE_POSTGRES if dialect == "postgresql" else _CREATE_TABLE_SQLITE
    op.execute(ddl)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nvda_qa_doc ON nvda_qa_results(document_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nvda_qa_status ON nvda_qa_results(overall_status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS nvda_qa_results")
