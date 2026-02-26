"""SQLite database layer for the WCAG PDF Remediation Pipeline POC.

Thread-safe singleton over sqlite3. JSON fields use json.dumps/loads.
Timestamps are ISO-8601 UTC strings.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


_DDL = """\
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
    token_hash TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    hash_algorithm TEXT NOT NULL DEFAULT 'sha256',
    token_expires_at TEXT);
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
CREATE INDEX IF NOT EXISTS idx_docs_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_findings_doc ON wcag_findings(document_id);
CREATE INDEX IF NOT EXISTS idx_review_doc ON review_items(document_id);
CREATE INDEX IF NOT EXISTS idx_review_decision ON review_items(reviewer_decision);
CREATE INDEX IF NOT EXISTS idx_proposals_doc ON change_proposals(document_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON change_proposals(status);
CREATE INDEX IF NOT EXISTS idx_rules_status ON rules_ledger(status);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE TABLE IF NOT EXISTS remediation_events (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, task_id TEXT NOT NULL,
    component TEXT NOT NULL, element_id TEXT NOT NULL DEFAULT '',
    before_value TEXT, after_value TEXT,
    source TEXT NOT NULL DEFAULT 'pipeline', timestamp TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_remed_task ON remediation_events(task_id);
CREATE INDEX IF NOT EXISTS idx_remed_doc ON remediation_events(document_id);
CREATE TABLE IF NOT EXISTS pipeline_telemetry (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    -- Timing (seconds)
    started_at TEXT NOT NULL,
    completed_at TEXT,
    total_duration_s REAL,
    extract_duration_s REAL,
    ai_duration_s REAL,
    build_html_duration_s REAL,
    validate_duration_s REAL,
    output_duration_s REAL,
    -- Extraction metrics
    blocks_extracted INTEGER DEFAULT 0,
    images_found INTEGER DEFAULT 0,
    tables_found INTEGER DEFAULT 0,
    headings_found INTEGER DEFAULT 0,
    artifacts_filtered INTEGER DEFAULT 0,
    -- AI metrics
    ai_model TEXT,
    ai_alt_text_attempted INTEGER DEFAULT 0,
    ai_alt_text_succeeded INTEGER DEFAULT 0,
    ai_alt_text_failed INTEGER DEFAULT 0,
    ai_table_attempted INTEGER DEFAULT 0,
    ai_table_succeeded INTEGER DEFAULT 0,
    -- Validation metrics
    gate_g1_passed INTEGER,
    gate_g3_passed INTEGER,
    axe_score REAL,
    axe_violations_critical INTEGER DEFAULT 0,
    axe_violations_serious INTEGER DEFAULT 0,
    validation_blocked INTEGER DEFAULT 0,
    -- Output metrics
    output_format TEXT,
    output_size_bytes INTEGER DEFAULT 0,
    output_method TEXT,
    -- Status
    status TEXT NOT NULL DEFAULT 'running',
    error_message TEXT,
    error_stage TEXT
);
CREATE INDEX IF NOT EXISTS idx_telemetry_doc ON pipeline_telemetry(document_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_status ON pipeline_telemetry(status);
CREATE TABLE IF NOT EXISTS image_assets (
    image_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    page_num INTEGER NOT NULL DEFAULT 0,
    mime_type TEXT NOT NULL DEFAULT 'image/png',
    image_data BLOB NOT NULL,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_img_doc ON image_assets(document_id);
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
CREATE INDEX IF NOT EXISTS idx_baseline_task ON baseline_validations(task_id);
CREATE INDEX IF NOT EXISTS idx_baseline_doc ON baseline_validations(document_id);
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
CREATE INDEX IF NOT EXISTS idx_altp_task ON alt_text_proposals(task_id);
CREATE INDEX IF NOT EXISTS idx_altp_doc ON alt_text_proposals(document_id);
CREATE INDEX IF NOT EXISTS idx_altp_status ON alt_text_proposals(status);
"""

# JSON fields that must be auto-decoded on read
_JSON_FIELDS: dict[str, list[str]] = {
    "review_items": ["original_content"],
    "change_proposals": ["system_evaluation", "patch_plan", "post_validation_result"],
    "rules_ledger": ["action", "validated_on_docs"],
}


def _enc(v: Any) -> str | None:
    """Encode a value to JSON string; pass through str and None unchanged."""
    if v is None or isinstance(v, str):
        return v
    return json.dumps(v)


def _decode_row(table: str, row: dict) -> dict:
    """Decode JSON fields for a given table in-place."""
    for field in _JSON_FIELDS.get(table, []):
        if row.get(field):
            try:
                row[field] = json.loads(row[field])
            except (json.JSONDecodeError, TypeError) as exc:
                raw = row[field]
                truncated = str(raw)[:120] if raw else ""
                logger.warning(
                    "JSON decode failed: table=%s field=%s error=%s raw_value=%.120s",
                    table, field, exc, truncated,
                )
    return row


class Database:
    """Database wrapper for the WCAG pipeline.

    Supports SQLite (default) and PostgreSQL via the backend abstraction layer.
    When db_path is provided (including ":memory:"), uses SQLite directly.
    When a backend object is provided, uses it instead.
    """

    def __init__(
        self,
        db_path: str = "wcag_pipeline.db",
        backend: Any = None,
    ) -> None:
        if backend is not None:
            self._backend = backend
        else:
            # Default: SQLite (backward compatible)
            from services.common.db_backend import SQLiteBackend
            self._backend = SQLiteBackend(db_path)

        self._backend.execute_pragma("PRAGMA journal_mode=WAL;")
        self._backend.execute_pragma("PRAGMA foreign_keys=ON;")

        # Use Postgres DDL if backend is postgres, otherwise SQLite DDL
        if self._backend.backend_type == "postgres":
            from services.common.db_backend import _POSTGRES_DDL
            self._backend.execute_ddl(_POSTGRES_DDL)
        else:
            self._backend.execute_ddl(_DDL)

        # Detect Alembic migrations state: if alembic_version table exists,
        # log a warning so operators know to run migrations separately.
        # We do NOT auto-run Alembic here — that is an operator action.
        try:
            row = self._backend.fetchone(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
                if self._backend.backend_type != "postgres"
                else "SELECT table_name FROM information_schema.tables "
                     "WHERE table_name='alembic_version'"
            )
            if row is None:
                logger.warning(
                    "alembic_version table not found — Alembic migrations have not been "
                    "applied. Run 'alembic upgrade head' to initialise the migration history."
                )
        except Exception as exc:
            logger.debug("Could not check alembic_version table: %s", exc)

    def _one(self, sql: str, p: tuple = (), table: str = "") -> dict | None:
        row = self._backend.fetchone(sql, p)
        if row is None:
            return None
        return _decode_row(table, row)

    def _all(self, sql: str, p: tuple = (), table: str = "") -> list[dict]:
        rows = self._backend.fetchall(sql, p)
        return [_decode_row(table, r) for r in rows]

    def _run(self, sql: str, p: tuple = ()) -> None:
        self._backend.execute(sql, p)
        self._backend.commit()

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    def insert_document(self, doc_id: str, filename: str, status: str = "queued",
                        page_count: int = 0, gcs_input_path: str = "") -> dict:
        now = _now()
        self._run(
            "INSERT INTO documents (id,filename,gcs_input_path,status,page_count,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (doc_id, filename, gcs_input_path, status, page_count, now, now),
        )
        self.log_audit("document", doc_id, "insert")
        return self.get_document(doc_id)  # type: ignore[return-value]

    def get_document(self, doc_id: str) -> dict | None:
        return self._one("SELECT * FROM documents WHERE id=?", (doc_id,))

    def list_documents(self, skip: int = 0, limit: int = 50,
                       status: str | None = None) -> list[dict]:
        if status:
            return self._all(
                "SELECT * FROM documents WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, skip),
            )
        return self._all(
            "SELECT * FROM documents ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, skip),
        )

    def update_document_status(self, doc_id: str, status: str,
                                **kwargs: Any) -> dict | None:
        old = self.get_document(doc_id)
        if old is None:
            return None
        allowed = {"gcs_output_path", "page_count"}
        sets = ["status=?", "updated_at=?"]
        params: list[Any] = [status, _now()]
        for col, val in kwargs.items():
            if col in allowed:
                sets.append(f"{col}=?")
                params.append(val)
        params.append(doc_id)
        self._run(f"UPDATE documents SET {','.join(sets)} WHERE id=?", tuple(params))
        self.log_audit("document", doc_id, "status_update",
                       old_value=old["status"], new_value=status)
        return self.get_document(doc_id)

    # ------------------------------------------------------------------
    # WCAG Findings
    # ------------------------------------------------------------------

    def insert_finding(self, finding_id: str, document_id: str, element_id: str,
                       criterion: str, severity: str, description: str,
                       suggested_fix: str | None = None, ai_draft: str | None = None,
                       complexity: str = "simple") -> dict:
        self._run(
            "INSERT INTO wcag_findings"
            " (id,document_id,element_id,criterion,severity,description,suggested_fix,ai_draft,complexity)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (finding_id, document_id, element_id, criterion, severity,
             description, suggested_fix, ai_draft, complexity),
        )
        return self._one("SELECT * FROM wcag_findings WHERE id=?", (finding_id,))  # type: ignore[return-value]

    def get_findings(self, document_id: str) -> list[dict]:
        return self._all("SELECT * FROM wcag_findings WHERE document_id=?", (document_id,))

    # ------------------------------------------------------------------
    # Review Items
    # ------------------------------------------------------------------

    def insert_review_item(self, item_id: str, document_id: str, finding_id: str,
                           element_type: str, original_content: dict,
                           ai_suggestion: str) -> dict:
        self._run(
            "INSERT INTO review_items"
            " (id,document_id,finding_id,element_type,original_content,ai_suggestion)"
            " VALUES (?,?,?,?,?,?)",
            (item_id, document_id, finding_id, element_type,
             json.dumps(original_content), ai_suggestion),
        )
        self.log_audit("review_item", item_id, "insert")
        return self._one("SELECT * FROM review_items WHERE id=?", (item_id,),
                         table="review_items")  # type: ignore[return-value]

    def get_review_items(self, document_id: str) -> list[dict]:
        return self._all("SELECT * FROM review_items WHERE document_id=?",
                         (document_id,), table="review_items")

    def get_pending_review_items(self, skip: int = 0, limit: int = 50) -> list[dict]:
        return self._all(
            "SELECT * FROM review_items WHERE reviewer_decision IS NULL"
            " ORDER BY rowid LIMIT ? OFFSET ?",
            (limit, skip), table="review_items",
        )

    def update_review_decision(self, item_id: str, decision: str,
                                edit: str | None = None,
                                reviewer_id: str | None = None,
                                expected_current_decision: str | None = None) -> dict | None:
        """Update the reviewer decision on a review item.

        Uses optimistic concurrency: the UPDATE includes the expected current
        state in the WHERE clause and checks rows_affected to detect conflicts.

        Args:
            item_id: The review item ID to update.
            decision: The new decision value ("approve", "edit", "reject").
            edit: Optional edited text from the reviewer.
            reviewer_id: Username or ID of the reviewer.
            expected_current_decision: If provided, the UPDATE only proceeds if
                the current reviewer_decision matches this value. Pass None to
                allow update regardless of current state (backward compatible).

        Returns:
            Updated row dict, or None if the item does not exist or a
            concurrency conflict was detected.
        """
        # Read current value for audit log only — not used for the update gate
        old = self._one("SELECT reviewer_decision FROM review_items WHERE id=?", (item_id,))
        if old is None:
            return None

        # Build WHERE clause to include expected current state (optimistic lock)
        if expected_current_decision is not None:
            sql = (
                "UPDATE review_items SET reviewer_decision=?,reviewer_edit=?,"
                "reviewed_at=?,reviewed_by=? WHERE id=? AND reviewer_decision=?"
            )
            params = (decision, edit, _now(), reviewer_id, item_id, expected_current_decision)
        else:
            sql = (
                "UPDATE review_items SET reviewer_decision=?,reviewer_edit=?,"
                "reviewed_at=?,reviewed_by=? WHERE id=?"
            )
            params = (decision, edit, _now(), reviewer_id, item_id)

        cur = self._backend.execute(sql, params)
        self._backend.commit()

        if cur.rowcount == 0:
            logger.warning(
                "update_review_decision: 0 rows updated for item_id=%s "
                "(expected_decision=%r, actual_decision=%r) — possible concurrency conflict",
                item_id, expected_current_decision, old.get("reviewer_decision"),
            )
            return None

        self.log_audit("review_item", item_id, "decision", performed_by=reviewer_id,
                       old_value=old["reviewer_decision"],
                       new_value=decision)
        return self._one("SELECT * FROM review_items WHERE id=?", (item_id,),
                         table="review_items")

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def create_user(self, username: str, display_name: str,
                    role: str, token_hash: str,
                    hash_algorithm: str = "sha256",
                    token_expires_at: str | None = None) -> str:
        uid = _new_id()
        self._run(
            "INSERT INTO users"
            " (id,username,display_name,role,token_hash,created_at,hash_algorithm,token_expires_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (uid, username, display_name, role, token_hash, _now(), hash_algorithm, token_expires_at),
        )
        return uid

    def upsert_user(self, username: str, display_name: str,
                    role: str, token_hash: str,
                    hash_algorithm: str = "sha256",
                    token_expires_at: str | None = None) -> str:
        """Insert or replace a user record keyed by username."""
        existing = self._one("SELECT id FROM users WHERE username=?", (username,))
        if existing:
            uid = existing["id"]
            self._run(
                "UPDATE users SET display_name=?,role=?,token_hash=?,hash_algorithm=?,"
                "token_expires_at=?,active=1 WHERE id=?",
                (display_name, role, token_hash, hash_algorithm, token_expires_at, uid),
            )
        else:
            uid = _new_id()
            self._run(
                "INSERT INTO users"
                " (id,username,display_name,role,token_hash,created_at,hash_algorithm,token_expires_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (uid, username, display_name, role, token_hash, _now(), hash_algorithm, token_expires_at),
            )
        return uid

    def get_user_by_token(self, token_hash: str) -> dict | None:
        return self._one(
            "SELECT * FROM users WHERE token_hash=? AND active=1", (token_hash,)
        )

    # ------------------------------------------------------------------
    # Change Proposals
    # ------------------------------------------------------------------

    def insert_proposal(self, proposal_id: str, document_id: str, proposed_by: str,
                        human_comment: str, system_evaluation: Any,
                        system_recommendation: str,
                        review_item_id: str | None = None) -> dict:
        self._run(
            "INSERT INTO change_proposals"
            " (id,document_id,review_item_id,proposed_by,human_comment,"
            "system_evaluation,system_recommendation,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (proposal_id, document_id, review_item_id, proposed_by, human_comment,
             _enc(system_evaluation), system_recommendation, _now()),
        )
        return self.get_proposal(proposal_id)  # type: ignore[return-value]

    def get_proposal(self, proposal_id: str) -> dict | None:
        return self._one("SELECT * FROM change_proposals WHERE id=?",
                         (proposal_id,), table="change_proposals")

    def list_proposals(self, document_id: str | None = None,
                       status: str | None = None) -> list[dict]:
        clauses, params = [], []
        if document_id:
            clauses.append("document_id=?"); params.append(document_id)
        if status:
            clauses.append("status=?"); params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._all(
            f"SELECT * FROM change_proposals {where} ORDER BY created_at DESC",
            tuple(params), table="change_proposals",
        )

    def update_proposal_status(self, proposal_id: str, status: str,
                                **kwargs: Any) -> dict | None:
        allowed = {"patch_plan", "post_validation_result", "human_override", "resolved_by"}
        sets = ["status=?"]
        params: list[Any] = [status]
        if status in ("approved", "rejected", "resolved"):
            sets.append("resolved_at=?"); params.append(_now())
        for col, val in kwargs.items():
            if col not in allowed:
                continue
            sets.append(f"{col}=?")
            params.append(_enc(val) if col in ("patch_plan", "post_validation_result") else val)
        params.append(proposal_id)
        self._run(f"UPDATE change_proposals SET {','.join(sets)} WHERE id=?", tuple(params))
        return self.get_proposal(proposal_id)

    # ------------------------------------------------------------------
    # Rules Ledger
    # ------------------------------------------------------------------

    def insert_rule(self, rule_id: str, trigger_pattern: str, action: Any,
                    confidence: float = 0.5, created_from: str | None = None) -> dict:
        now = _now()
        self._run(
            "INSERT INTO rules_ledger"
            " (id,trigger_pattern,action,confidence,created_from,"
            "validated_on_docs,created_at,updated_at)"
            " VALUES (?,?,?,?,?,'[]',?,?)",
            (rule_id, trigger_pattern, _enc(action), confidence, created_from, now, now),
        )
        return self._one("SELECT * FROM rules_ledger WHERE id=?", (rule_id,),
                         table="rules_ledger")  # type: ignore[return-value]

    def get_active_rules(self) -> list[dict]:
        return self._all(
            "SELECT * FROM rules_ledger WHERE status='active' ORDER BY confidence DESC",
            table="rules_ledger",
        )

    def update_rule_status(self, rule_id: str, status: str) -> dict | None:
        self._run("UPDATE rules_ledger SET status=?,updated_at=? WHERE id=?",
                  (status, _now(), rule_id))
        return self._one("SELECT * FROM rules_ledger WHERE id=?", (rule_id,),
                         table="rules_ledger")

    def add_validated_doc(self, rule_id: str, document_id: str) -> dict | None:
        row = self._one("SELECT validated_on_docs FROM rules_ledger WHERE id=?", (rule_id,))
        if row is None:
            return None
        docs: list = row.get("validated_on_docs") or []
        if not isinstance(docs, list):
            try:
                docs = json.loads(docs)
            except (json.JSONDecodeError, TypeError):
                docs = []
        if document_id not in docs:
            docs.append(document_id)
        self._run("UPDATE rules_ledger SET validated_on_docs=?,updated_at=? WHERE id=?",
                  (json.dumps(docs), _now(), rule_id))
        return self._one("SELECT * FROM rules_ledger WHERE id=?", (rule_id,),
                         table="rules_ledger")

    # ------------------------------------------------------------------
    # Audit Log
    # ------------------------------------------------------------------

    def log_audit(self, entity_type: str, entity_id: str, action: str,
                  performed_by: str | None = None,
                  old_value: Any = None, new_value: Any = None) -> None:
        self._run(
            "INSERT INTO audit_log"
            " (entity_type,entity_id,action,performed_by,old_value,new_value,timestamp)"
            " VALUES (?,?,?,?,?,?,?)",
            (entity_type, entity_id, action, performed_by,
             _enc(old_value), _enc(new_value), _now()),
        )

    def get_audit_log(self, entity_type: str, entity_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM audit_log WHERE entity_type=? AND entity_id=?"
            " ORDER BY timestamp ASC",
            (entity_type, entity_id),
        )

    # ------------------------------------------------------------------
    # Remediation Events
    # ------------------------------------------------------------------

    def insert_remediation_event(
        self,
        event_id: str,
        document_id: str,
        task_id: str,
        component: str,
        element_id: str,
        before: Any,
        after: Any,
        source: str,
        timestamp: str,
    ) -> None:
        import json as _json
        self._run(
            "INSERT INTO remediation_events (id, document_id, task_id, component, "
            "element_id, before_value, after_value, source, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, document_id, task_id, component, element_id,
             _json.dumps(before), _json.dumps(after), source, timestamp),
        )

    def get_remediation_events(self, task_id: str) -> list[dict]:
        import json as _json
        rows = self._all(
            "SELECT * FROM remediation_events WHERE task_id = ? ORDER BY timestamp",
            (task_id,),
        )
        for row in rows:
            for key in ("before_value", "after_value"):
                if row.get(key):
                    try:
                        row[key] = _json.loads(row[key])
                    except (ValueError, TypeError):
                        pass
        return rows

    # ------------------------------------------------------------------
    # Pipeline Telemetry
    # ------------------------------------------------------------------

    def insert_telemetry(self, record: dict) -> None:
        """Insert a new telemetry record.

        Uses the dict keys as column names with a parameterized INSERT.
        Only keys that match actual table columns are written; unknown keys
        are silently ignored.
        """
        # Allowlist of valid column names to prevent SQL injection
        _valid_cols = {
            "id", "document_id", "task_id", "filename", "file_size_bytes",
            "page_count", "started_at", "completed_at", "total_duration_s",
            "extract_duration_s", "ai_duration_s", "build_html_duration_s",
            "validate_duration_s", "output_duration_s", "blocks_extracted",
            "images_found", "tables_found", "headings_found",
            "artifacts_filtered", "ai_model", "ai_alt_text_attempted",
            "ai_alt_text_succeeded", "ai_alt_text_failed",
            "ai_table_attempted", "ai_table_succeeded", "gate_g1_passed",
            "gate_g3_passed", "axe_score", "axe_violations_critical",
            "axe_violations_serious", "validation_blocked", "output_format",
            "output_size_bytes", "output_method", "status", "error_message",
            "error_stage",
        }
        filtered = {k: v for k, v in record.items() if k in _valid_cols}
        if not filtered:
            return
        cols = ", ".join(filtered.keys())
        placeholders = ", ".join("?" for _ in filtered)
        self._run(
            f"INSERT INTO pipeline_telemetry ({cols}) VALUES ({placeholders})",
            tuple(filtered.values()),
        )

    def update_telemetry(self, telemetry_id: str, updates: dict) -> None:
        """Update an existing telemetry record with new metrics."""
        _valid_cols = {
            "document_id", "task_id", "filename", "file_size_bytes",
            "page_count", "started_at", "completed_at", "total_duration_s",
            "extract_duration_s", "ai_duration_s", "build_html_duration_s",
            "validate_duration_s", "output_duration_s", "blocks_extracted",
            "images_found", "tables_found", "headings_found",
            "artifacts_filtered", "ai_model", "ai_alt_text_attempted",
            "ai_alt_text_succeeded", "ai_alt_text_failed",
            "ai_table_attempted", "ai_table_succeeded", "gate_g1_passed",
            "gate_g3_passed", "axe_score", "axe_violations_critical",
            "axe_violations_serious", "validation_blocked", "output_format",
            "output_size_bytes", "output_method", "status", "error_message",
            "error_stage",
        }
        filtered = {k: v for k, v in updates.items() if k in _valid_cols}
        if not filtered:
            return
        sets = ", ".join(f"{k}=?" for k in filtered)
        params = list(filtered.values()) + [telemetry_id]
        self._run(
            f"UPDATE pipeline_telemetry SET {sets} WHERE id=?",
            tuple(params),
        )

    def get_telemetry(self, telemetry_id: str) -> dict | None:
        """Get a single telemetry record."""
        return self._one(
            "SELECT * FROM pipeline_telemetry WHERE id=?", (telemetry_id,)
        )

    # ------------------------------------------------------------------
    # Image Assets (HITL preview)
    # ------------------------------------------------------------------

    def insert_image_asset(
        self,
        image_id: str,
        document_id: str,
        page_num: int,
        mime_type: str,
        image_data: bytes,
        width: int = 0,
        height: int = 0,
    ) -> str:
        """Persist image bytes for HITL preview. Returns image_id."""
        try:
            self._backend.execute(
                "INSERT OR REPLACE INTO image_assets "
                "(image_id, document_id, page_num, mime_type, image_data, width, height, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (image_id, document_id, page_num, mime_type, image_data, width, height, _now()),
            )
            self._backend.commit()
        except Exception:
            # Image storage must never crash the pipeline
            import logging
            logging.getLogger(__name__).warning(
                "Failed to store image asset %s", image_id, exc_info=True,
            )
        return image_id

    def get_image_asset(self, image_id: str) -> dict | None:
        """Retrieve image bytes by image_id. Returns dict with image_data BLOB."""
        return self._backend.fetchone(
            "SELECT * FROM image_assets WHERE image_id=?", (image_id,),
        )

    def delete_images_for_document(self, document_id: str) -> int:
        """Delete all image assets for a document. Returns deleted count."""
        cur = self._backend.execute(
            "DELETE FROM image_assets WHERE document_id=?", (document_id,),
        )
        self._backend.commit()
        return cur.rowcount

    def list_telemetry(self, limit: int = 50, status: str | None = None) -> list[dict]:
        """List telemetry records, newest first. Optionally filter by status."""
        if status:
            return self._all(
                "SELECT * FROM pipeline_telemetry WHERE status=? "
                "ORDER BY started_at DESC LIMIT ?",
                (status, limit),
            )
        return self._all(
            "SELECT * FROM pipeline_telemetry ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Baseline Validations
    # ------------------------------------------------------------------

    def insert_baseline_validation(
        self,
        task_id: str,
        document_id: str,
        pdf_size_bytes: int,
        is_compliant: bool,
        total_rules_checked: int,
        passed_rules: int,
        error_count: int,
        failed_clauses: list[str],
        failed_rules: list[dict],
        raw_response: dict | None = None,
    ) -> str:
        """Persist a VeraPDF baseline validation result. Returns the record id."""
        rec_id = _new_id()
        self._backend.execute(
            "INSERT INTO baseline_validations "
            "(id, task_id, document_id, pdf_size_bytes, is_compliant, "
            "total_rules_checked, passed_rules, error_count, "
            "failed_clauses, failed_rules_json, raw_response, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec_id, task_id, document_id, pdf_size_bytes,
                1 if is_compliant else 0,
                total_rules_checked, passed_rules, error_count,
                json.dumps(failed_clauses),
                json.dumps(failed_rules),
                json.dumps(raw_response) if raw_response else None,
                _now(),
            ),
        )
        self._backend.commit()
        return rec_id

    def get_baseline_validation(self, task_id: str) -> dict | None:
        """Retrieve the baseline validation for a given task_id."""
        row = self._one(
            "SELECT * FROM baseline_validations WHERE task_id=?",
            (task_id,),
        )
        if row is None:
            return None
        # Decode JSON fields
        for field in ("failed_clauses", "failed_rules_json", "raw_response"):
            if row.get(field):
                try:
                    row[field] = json.loads(row[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return row

    # ------------------------------------------------------------------
    # Alt Text Proposals
    # ------------------------------------------------------------------

    def insert_alt_text_proposal(
        self,
        task_id: str,
        document_id: str,
        image_id: str,
        block_id: str,
        page_num: int,
        original_alt: str,
        proposed_alt: str,
        image_classification: str = "informative",
        confidence: float = 0.0,
    ) -> str:
        """Insert an AI alt-text proposal. Returns the proposal id."""
        rec_id = _new_id()
        self._backend.execute(
            "INSERT INTO alt_text_proposals "
            "(id, task_id, document_id, image_id, block_id, page_num, "
            "original_alt, proposed_alt, image_classification, confidence, "
            "status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                rec_id, task_id, document_id, image_id, block_id, page_num,
                original_alt, proposed_alt, image_classification, confidence,
                _now(),
            ),
        )
        self._backend.commit()
        return rec_id

    def get_alt_text_proposals(self, task_id: str) -> list[dict]:
        """Retrieve all alt-text proposals for a task, newest first."""
        return self._all(
            "SELECT * FROM alt_text_proposals WHERE task_id=? ORDER BY page_num, created_at",
            (task_id,),
        )

    def get_alt_text_proposal(self, proposal_id: str) -> dict | None:
        """Retrieve a single alt-text proposal by id."""
        return self._one(
            "SELECT * FROM alt_text_proposals WHERE id=?",
            (proposal_id,),
        )

    def update_alt_text_proposal_decision(
        self,
        proposal_id: str,
        decision: str,
        reviewer_edit: str | None = None,
        reviewed_by: str | None = None,
    ) -> dict | None:
        """Record a reviewer decision on an alt-text proposal."""
        status = "approved" if decision in ("approve", "edit") else "rejected"
        self._backend.execute(
            "UPDATE alt_text_proposals SET reviewer_decision=?, reviewer_edit=?, "
            "reviewed_by=?, reviewed_at=?, status=? WHERE id=?",
            (decision, reviewer_edit, reviewed_by, _now(), status, proposal_id),
        )
        self._backend.commit()
        return self._one("SELECT * FROM alt_text_proposals WHERE id=?", (proposal_id,))

    def batch_approve_alt_text_proposals(
        self,
        proposal_ids: list[str],
        reviewed_by: str | None = None,
    ) -> int:
        """Batch-approve multiple alt-text proposals. Returns count updated."""
        now = _now()
        count = 0
        for pid in proposal_ids:
            cur = self._backend.execute(
                "UPDATE alt_text_proposals SET reviewer_decision='approve', "
                "status='approved', reviewed_by=?, reviewed_at=? "
                "WHERE id=? AND status='pending'",
                (reviewed_by, now, pid),
            )
            count += cur.rowcount
        self._backend.commit()
        return count


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: Database | None = None


def get_db(db_path: str = "wcag_pipeline.db") -> Database:
    """Return the module-level singleton Database instance.

    First call creates the instance with *db_path*; subsequent calls
    return the cached instance regardless of *db_path*.

    When WCAG_DB_BACKEND=postgres is set (via config), creates a
    PostgreSQL-backed instance instead of SQLite.
    """
    global _instance
    if _instance is None:
        from services.common.config import settings
        if settings.db_backend == "postgres" and settings.postgres_url:
            # Fail-fast: if postgres is configured but connection fails,
            # do NOT silently fall back to ephemeral SQLite (data loss on Cloud Run).
            from services.common.db_backend import create_backend
            backend = create_backend("postgres", postgres_url=settings.postgres_url)
            _instance = Database(backend=backend)
            return _instance
        _instance = Database(db_path)
    return _instance
