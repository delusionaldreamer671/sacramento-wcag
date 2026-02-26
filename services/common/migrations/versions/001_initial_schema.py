"""Initial schema — matches existing 13-table DDL.

This migration creates all tables that already exist in production via
CREATE TABLE IF NOT EXISTS in database.py. Using IF NOT EXISTS ensures
this migration is safe to run against an existing database (no-op for
tables that already exist).

For existing databases: run `alembic stamp 001` to mark as current
without executing any DDL.

Revision ID: 001
Revises: None
Create Date: 2026-02-26
"""
from __future__ import annotations

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

# ---------------------------------------------------------------------------
# SQLite DDL (default)
# ---------------------------------------------------------------------------

_SQLITE_DDL = """\
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, filename TEXT NOT NULL,
    gcs_input_path TEXT NOT NULL DEFAULT '', gcs_output_path TEXT,
    status TEXT NOT NULL DEFAULT 'queued', page_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wcag_findings (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
    element_id TEXT NOT NULL, criterion TEXT NOT NULL, severity TEXT NOT NULL,
    description TEXT NOT NULL, suggested_fix TEXT, ai_draft TEXT,
    complexity TEXT NOT NULL DEFAULT 'simple');
CREATE TABLE IF NOT EXISTS review_items (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
    finding_id TEXT NOT NULL, element_type TEXT NOT NULL,
    original_content TEXT NOT NULL, ai_suggestion TEXT NOT NULL,
    reviewer_decision TEXT, reviewer_edit TEXT, reviewed_at TEXT, reviewed_by TEXT);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'reviewer',
    token_hash TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS change_proposals (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
    review_item_id TEXT, proposed_by TEXT NOT NULL, human_comment TEXT NOT NULL,
    system_evaluation TEXT NOT NULL, system_recommendation TEXT NOT NULL,
    human_override INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
    patch_plan TEXT, post_validation_result TEXT,
    created_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT);
CREATE TABLE IF NOT EXISTS rules_ledger (
    id TEXT PRIMARY KEY, trigger_pattern TEXT NOT NULL, action TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5, created_from TEXT,
    validated_on_docs TEXT NOT NULL DEFAULT '[]',
    rollback_supported INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'candidate',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL, action TEXT NOT NULL, performed_by TEXT,
    old_value TEXT, new_value TEXT, timestamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS remediation_events (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, task_id TEXT NOT NULL,
    component TEXT NOT NULL, element_id TEXT NOT NULL DEFAULT '',
    before_value TEXT, after_value TEXT,
    source TEXT NOT NULL DEFAULT 'pipeline', timestamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pipeline_telemetry (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    total_duration_s REAL,
    extract_duration_s REAL,
    ai_duration_s REAL,
    build_html_duration_s REAL,
    validate_duration_s REAL,
    output_duration_s REAL,
    blocks_extracted INTEGER DEFAULT 0,
    images_found INTEGER DEFAULT 0,
    tables_found INTEGER DEFAULT 0,
    headings_found INTEGER DEFAULT 0,
    artifacts_filtered INTEGER DEFAULT 0,
    ai_model TEXT,
    ai_alt_text_attempted INTEGER DEFAULT 0,
    ai_alt_text_succeeded INTEGER DEFAULT 0,
    ai_alt_text_failed INTEGER DEFAULT 0,
    ai_table_attempted INTEGER DEFAULT 0,
    ai_table_succeeded INTEGER DEFAULT 0,
    gate_g1_passed INTEGER,
    gate_g3_passed INTEGER,
    axe_score REAL,
    axe_violations_critical INTEGER DEFAULT 0,
    axe_violations_serious INTEGER DEFAULT 0,
    validation_blocked INTEGER DEFAULT 0,
    output_format TEXT,
    output_size_bytes INTEGER DEFAULT 0,
    output_method TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    error_message TEXT,
    error_stage TEXT);
CREATE TABLE IF NOT EXISTS image_assets (
    image_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    page_num INTEGER NOT NULL DEFAULT 0,
    mime_type TEXT NOT NULL DEFAULT 'image/png',
    image_data BLOB NOT NULL,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS baseline_validations (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    document_id TEXT NOT NULL DEFAULT '',
    pdf_size_bytes INTEGER NOT NULL DEFAULT 0,
    is_compliant INTEGER NOT NULL DEFAULT 0,
    total_rules_checked INTEGER NOT NULL DEFAULT 0,
    passed_rules INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    failed_clauses TEXT NOT NULL DEFAULT '[]',
    failed_rules_json TEXT NOT NULL DEFAULT '[]',
    raw_response TEXT,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alt_text_proposals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    document_id TEXT NOT NULL DEFAULT '',
    image_id TEXT NOT NULL DEFAULT '',
    block_id TEXT NOT NULL DEFAULT '',
    page_num INTEGER NOT NULL DEFAULT 0,
    original_alt TEXT NOT NULL DEFAULT '',
    proposed_alt TEXT NOT NULL DEFAULT '',
    image_classification TEXT NOT NULL DEFAULT 'informative',
    confidence REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_decision TEXT,
    reviewer_edit TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL);
"""

# ---------------------------------------------------------------------------
# PostgreSQL DDL (audit_log uses SERIAL, image_assets uses BYTEA)
# ---------------------------------------------------------------------------

_POSTGRES_DDL = """\
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, filename TEXT NOT NULL,
    gcs_input_path TEXT NOT NULL DEFAULT '', gcs_output_path TEXT,
    status TEXT NOT NULL DEFAULT 'queued', page_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wcag_findings (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
    element_id TEXT NOT NULL, criterion TEXT NOT NULL, severity TEXT NOT NULL,
    description TEXT NOT NULL, suggested_fix TEXT, ai_draft TEXT,
    complexity TEXT NOT NULL DEFAULT 'simple');
CREATE TABLE IF NOT EXISTS review_items (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
    finding_id TEXT NOT NULL, element_type TEXT NOT NULL,
    original_content TEXT NOT NULL, ai_suggestion TEXT NOT NULL,
    reviewer_decision TEXT, reviewer_edit TEXT, reviewed_at TEXT, reviewed_by TEXT);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'reviewer',
    token_hash TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS change_proposals (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
    review_item_id TEXT, proposed_by TEXT NOT NULL, human_comment TEXT NOT NULL,
    system_evaluation TEXT NOT NULL, system_recommendation TEXT NOT NULL,
    human_override INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
    patch_plan TEXT, post_validation_result TEXT,
    created_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT);
CREATE TABLE IF NOT EXISTS rules_ledger (
    id TEXT PRIMARY KEY, trigger_pattern TEXT NOT NULL, action TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5, created_from TEXT,
    validated_on_docs TEXT NOT NULL DEFAULT '[]',
    rollback_supported INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'candidate',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY, entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL, action TEXT NOT NULL, performed_by TEXT,
    old_value TEXT, new_value TEXT, timestamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS remediation_events (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, task_id TEXT NOT NULL,
    component TEXT NOT NULL, element_id TEXT NOT NULL DEFAULT '',
    before_value TEXT, after_value TEXT,
    source TEXT NOT NULL DEFAULT 'pipeline', timestamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pipeline_telemetry (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    total_duration_s REAL,
    extract_duration_s REAL,
    ai_duration_s REAL,
    build_html_duration_s REAL,
    validate_duration_s REAL,
    output_duration_s REAL,
    blocks_extracted INTEGER DEFAULT 0,
    images_found INTEGER DEFAULT 0,
    tables_found INTEGER DEFAULT 0,
    headings_found INTEGER DEFAULT 0,
    artifacts_filtered INTEGER DEFAULT 0,
    ai_model TEXT,
    ai_alt_text_attempted INTEGER DEFAULT 0,
    ai_alt_text_succeeded INTEGER DEFAULT 0,
    ai_alt_text_failed INTEGER DEFAULT 0,
    ai_table_attempted INTEGER DEFAULT 0,
    ai_table_succeeded INTEGER DEFAULT 0,
    gate_g1_passed INTEGER,
    gate_g3_passed INTEGER,
    axe_score REAL,
    axe_violations_critical INTEGER DEFAULT 0,
    axe_violations_serious INTEGER DEFAULT 0,
    validation_blocked INTEGER DEFAULT 0,
    output_format TEXT,
    output_size_bytes INTEGER DEFAULT 0,
    output_method TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    error_message TEXT,
    error_stage TEXT);
CREATE TABLE IF NOT EXISTS image_assets (
    image_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    page_num INTEGER NOT NULL DEFAULT 0,
    mime_type TEXT NOT NULL DEFAULT 'image/png',
    image_data BYTEA NOT NULL,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS baseline_validations (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    document_id TEXT NOT NULL DEFAULT '',
    pdf_size_bytes INTEGER NOT NULL DEFAULT 0,
    is_compliant INTEGER NOT NULL DEFAULT 0,
    total_rules_checked INTEGER NOT NULL DEFAULT 0,
    passed_rules INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    failed_clauses TEXT NOT NULL DEFAULT '[]',
    failed_rules_json TEXT NOT NULL DEFAULT '[]',
    raw_response TEXT,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alt_text_proposals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    document_id TEXT NOT NULL DEFAULT '',
    image_id TEXT NOT NULL DEFAULT '',
    block_id TEXT NOT NULL DEFAULT '',
    page_num INTEGER NOT NULL DEFAULT 0,
    original_alt TEXT NOT NULL DEFAULT '',
    proposed_alt TEXT NOT NULL DEFAULT '',
    image_classification TEXT NOT NULL DEFAULT 'informative',
    confidence REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_decision TEXT,
    reviewer_edit TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL);
"""

# ---------------------------------------------------------------------------
# Indexes (same for both backends)
# ---------------------------------------------------------------------------

_INDEXES = """\
CREATE INDEX IF NOT EXISTS idx_docs_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_findings_doc ON wcag_findings(document_id);
CREATE INDEX IF NOT EXISTS idx_review_doc ON review_items(document_id);
CREATE INDEX IF NOT EXISTS idx_review_decision ON review_items(reviewer_decision);
CREATE INDEX IF NOT EXISTS idx_proposals_doc ON change_proposals(document_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON change_proposals(status);
CREATE INDEX IF NOT EXISTS idx_rules_status ON rules_ledger(status);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_remed_task ON remediation_events(task_id);
CREATE INDEX IF NOT EXISTS idx_remed_doc ON remediation_events(document_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_doc ON pipeline_telemetry(document_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_status ON pipeline_telemetry(status);
CREATE INDEX IF NOT EXISTS idx_img_doc ON image_assets(document_id);
CREATE INDEX IF NOT EXISTS idx_baseline_task ON baseline_validations(task_id);
CREATE INDEX IF NOT EXISTS idx_baseline_doc ON baseline_validations(document_id);
CREATE INDEX IF NOT EXISTS idx_altp_task ON alt_text_proposals(task_id);
CREATE INDEX IF NOT EXISTS idx_altp_doc ON alt_text_proposals(document_id);
CREATE INDEX IF NOT EXISTS idx_altp_status ON alt_text_proposals(status);
"""


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    ddl = _POSTGRES_DDL if dialect == "postgresql" else _SQLITE_DDL
    for statement in ddl.strip().split(";"):
        stmt = statement.strip()
        if stmt:
            op.execute(stmt)
    for statement in _INDEXES.strip().split(";"):
        stmt = statement.strip()
        if stmt:
            op.execute(stmt)


def downgrade() -> None:
    tables = [
        "alt_text_proposals",
        "baseline_validations",
        "image_assets",
        "pipeline_telemetry",
        "remediation_events",
        "audit_log",
        "rules_ledger",
        "change_proposals",
        "review_items",
        "wcag_findings",
        "users",
        "documents",
    ]
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table}")
