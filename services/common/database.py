"""SQLite database layer for the WCAG PDF Remediation Pipeline POC.

Thread-safe singleton over sqlite3. JSON fields use json.dumps/loads.
Timestamps are ISO-8601 UTC strings.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


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
            except (json.JSONDecodeError, TypeError):
                pass
    return row


class Database:
    """Thread-safe SQLite wrapper for the WCAG pipeline."""

    def __init__(self, db_path: str = "wcag_pipeline.db") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_DDL)
        self._conn.commit()

    def _one(self, sql: str, p: tuple = (), table: str = "") -> dict | None:
        row = self._conn.execute(sql, p).fetchone()
        if row is None:
            return None
        return _decode_row(table, dict(row))

    def _all(self, sql: str, p: tuple = (), table: str = "") -> list[dict]:
        rows = [dict(r) for r in self._conn.execute(sql, p).fetchall()]
        return [_decode_row(table, r) for r in rows]

    def _run(self, sql: str, p: tuple = ()) -> None:
        self._conn.execute(sql, p)
        self._conn.commit()

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
                                reviewer_id: str | None = None) -> dict | None:
        old = self._one("SELECT reviewer_decision FROM review_items WHERE id=?", (item_id,))
        self._run(
            "UPDATE review_items SET reviewer_decision=?,reviewer_edit=?,"
            "reviewed_at=?,reviewed_by=? WHERE id=?",
            (decision, edit, _now(), reviewer_id, item_id),
        )
        self.log_audit("review_item", item_id, "decision", performed_by=reviewer_id,
                       old_value=old["reviewer_decision"] if old else None,
                       new_value=decision)
        return self._one("SELECT * FROM review_items WHERE id=?", (item_id,),
                         table="review_items")

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def create_user(self, username: str, display_name: str,
                    role: str, token_hash: str) -> str:
        uid = _new_id()
        self._run(
            "INSERT INTO users (id,username,display_name,role,token_hash,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (uid, username, display_name, role, token_hash, _now()),
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


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: Database | None = None


def get_db(db_path: str = "wcag_pipeline.db") -> Database:
    """Return the module-level singleton Database instance.

    First call creates the instance with *db_path*; subsequent calls
    return the cached instance regardless of *db_path*.
    """
    global _instance
    if _instance is None:
        _instance = Database(db_path)
    return _instance
