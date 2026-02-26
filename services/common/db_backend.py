"""Database backend abstraction layer.

Provides a protocol-based interface for swapping between SQLite and PostgreSQL
without changing the SQL queries in database.py. The backend handles:
- Connection creation and lifecycle
- Parameter placeholder translation (? → %s for Postgres)
- Row factory (dict rows)
- PRAGMA handling (SQLite-only)
- DDL execution

Usage:
    from services.common.db_backend import create_backend

    backend = create_backend("sqlite", db_path="wcag_pipeline.db")
    # or
    backend = create_backend("postgres", postgres_url="postgresql://...")
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DatabaseBackend(Protocol):
    """Abstract interface for database backends."""

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute a single SQL statement with parameter substitution."""
        ...

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        """Execute a SQL statement for each set of parameters."""
        ...

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        """Execute and return a single row as a dict, or None."""
        ...

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute and return all rows as dicts."""
        ...

    def commit(self) -> None:
        """Commit the current transaction."""
        ...

    def execute_ddl(self, ddl: str) -> None:
        """Execute DDL statements (CREATE TABLE, etc.)."""
        ...

    def execute_pragma(self, pragma: str) -> None:
        """Execute a PRAGMA (SQLite-only; no-op for Postgres)."""
        ...

    @property
    def backend_type(self) -> str:
        """Return 'sqlite' or 'postgres'."""
        ...


# ---------------------------------------------------------------------------
# SQLite Backend
# ---------------------------------------------------------------------------


class SQLiteBackend:
    """SQLite backend — wraps sqlite3 connection."""

    def __init__(self, db_path: str = "wcag_pipeline.db") -> None:
        import sqlite3
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql: str, params: tuple = ()) -> Any:
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        self._conn.executemany(sql, params_list)

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def commit(self) -> None:
        self._conn.commit()

    def execute_ddl(self, ddl: str) -> None:
        self._conn.executescript(ddl)
        self._conn.commit()

    def execute_pragma(self, pragma: str) -> None:
        self._conn.execute(pragma)

    @property
    def backend_type(self) -> str:
        return "sqlite"


# ---------------------------------------------------------------------------
# PostgreSQL Backend
# ---------------------------------------------------------------------------

# Regex to translate SQLite ? placeholders to PostgreSQL %s
_PLACEHOLDER_RE = re.compile(r"\?")


def _translate_params(sql: str) -> str:
    """Convert SQLite-style ? placeholders to PostgreSQL-style %s."""
    return _PLACEHOLDER_RE.sub("%s", sql)


# PostgreSQL DDL — equivalent to the SQLite _DDL but with Postgres syntax
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


class PostgresBackend:
    """PostgreSQL backend using psycopg 3."""

    def __init__(self, postgres_url: str) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required for PostgreSQL backend. "
                "Install with: pip install 'psycopg[binary]>=3.1.0'"
            ) from exc

        self._conn = psycopg.connect(postgres_url, autocommit=False)
        # Use dict rows
        from psycopg.rows import dict_row
        self._conn.row_factory = dict_row

    def execute(self, sql: str, params: tuple = ()) -> Any:
        translated = _translate_params(sql)
        return self._conn.execute(translated, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        translated = _translate_params(sql)
        self._conn.executemany(translated, params_list)

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        translated = _translate_params(sql)
        cur = self._conn.execute(translated, params)
        row = cur.fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        translated = _translate_params(sql)
        cur = self._conn.execute(translated, params)
        return [dict(r) for r in cur.fetchall()]

    def commit(self) -> None:
        self._conn.commit()

    def execute_ddl(self, ddl: str) -> None:
        # PostgreSQL: execute each statement separately
        for statement in ddl.split(";"):
            stmt = statement.strip()
            if stmt:
                self._conn.execute(stmt)
        self._conn.commit()

    def execute_pragma(self, pragma: str) -> None:
        # PRAGMAs are SQLite-only — no-op for Postgres
        pass

    @property
    def backend_type(self) -> str:
        return "postgres"

    def upsert_image(
        self,
        sql_columns: str,
        values: tuple,
    ) -> None:
        """PostgreSQL-specific upsert for image_assets using ON CONFLICT."""
        cols = sql_columns
        placeholders = ", ".join(["%s"] * len(values))
        sql = (
            f"INSERT INTO image_assets ({cols}) VALUES ({placeholders}) "
            "ON CONFLICT (image_id) DO UPDATE SET "
            "document_id=EXCLUDED.document_id, page_num=EXCLUDED.page_num, "
            "mime_type=EXCLUDED.mime_type, image_data=EXCLUDED.image_data, "
            "width=EXCLUDED.width, height=EXCLUDED.height, "
            "created_at=EXCLUDED.created_at"
        )
        self._conn.execute(sql, values)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_backend(
    backend_type: str = "sqlite",
    db_path: str = "wcag_pipeline.db",
    postgres_url: str = "",
) -> SQLiteBackend | PostgresBackend:
    """Create and return the appropriate database backend."""
    if backend_type == "postgres":
        if not postgres_url:
            raise ValueError("postgres_url is required for PostgreSQL backend")
        return PostgresBackend(postgres_url)
    return SQLiteBackend(db_path)
