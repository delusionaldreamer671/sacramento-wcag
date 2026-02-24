"""Tests for the SQLite database layer."""

from __future__ import annotations

import json
import os
import tempfile
import uuid

import pytest

from services.common.database import Database


@pytest.fixture
def db():
    """Create a fresh in-memory database for each test."""
    return Database(":memory:")


@pytest.fixture
def doc_id():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class TestDocuments:
    def test_insert_and_get_document(self, db: Database, doc_id: str):
        result = db.insert_document(doc_id, "test.pdf", page_count=5)
        assert result["id"] == doc_id
        assert result["filename"] == "test.pdf"
        assert result["status"] == "queued"
        assert result["page_count"] == 5

    def test_get_nonexistent_document(self, db: Database):
        assert db.get_document("nonexistent") is None

    def test_list_documents_empty(self, db: Database):
        docs = db.list_documents()
        assert docs == []

    def test_list_documents_with_filter(self, db: Database):
        db.insert_document(str(uuid.uuid4()), "a.pdf", status="queued")
        db.insert_document(str(uuid.uuid4()), "b.pdf", status="complete")
        db.insert_document(str(uuid.uuid4()), "c.pdf", status="queued")

        queued = db.list_documents(status="queued")
        assert len(queued) == 2

        complete = db.list_documents(status="complete")
        assert len(complete) == 1

    def test_list_documents_pagination(self, db: Database):
        for i in range(10):
            db.insert_document(str(uuid.uuid4()), f"doc_{i}.pdf")

        page1 = db.list_documents(skip=0, limit=3)
        assert len(page1) == 3

        page2 = db.list_documents(skip=3, limit=3)
        assert len(page2) == 3

    def test_update_document_status(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        updated = db.update_document_status(doc_id, "extracting")
        assert updated["status"] == "extracting"

    def test_update_document_with_extra_fields(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        updated = db.update_document_status(
            doc_id, "complete", page_count=10, gcs_output_path="gs://bucket/out.pdf"
        )
        assert updated["page_count"] == 10
        assert updated["gcs_output_path"] == "gs://bucket/out.pdf"

    def test_update_nonexistent_document(self, db: Database):
        result = db.update_document_status("nonexistent", "failed")
        assert result is None


# ---------------------------------------------------------------------------
# WCAG Findings
# ---------------------------------------------------------------------------


class TestFindings:
    def test_insert_and_get_findings(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        finding_id = str(uuid.uuid4())
        result = db.insert_finding(
            finding_id, doc_id, "elem-1", "1.1.1", "critical", "Missing alt text"
        )
        assert result["id"] == finding_id
        assert result["criterion"] == "1.1.1"

        findings = db.get_findings(doc_id)
        assert len(findings) == 1

    def test_get_findings_empty(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        assert db.get_findings(doc_id) == []


# ---------------------------------------------------------------------------
# Review Items
# ---------------------------------------------------------------------------


class TestReviewItems:
    def test_insert_review_item_json_roundtrip(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        item_id = str(uuid.uuid4())
        original = {"path": "//Document/Figure", "alt": ""}
        result = db.insert_review_item(
            item_id, doc_id, "finding-1", "image", original, "AI alt text suggestion"
        )
        assert result["original_content"] == original
        assert result["ai_suggestion"] == "AI alt text suggestion"

    def test_get_pending_review_items(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        db.insert_review_item(str(uuid.uuid4()), doc_id, "f1", "image", {}, "suggestion1")
        db.insert_review_item(str(uuid.uuid4()), doc_id, "f2", "table", {}, "suggestion2")

        pending = db.get_pending_review_items()
        assert len(pending) == 2

    def test_update_review_decision(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        item_id = str(uuid.uuid4())
        db.insert_review_item(item_id, doc_id, "f1", "image", {}, "suggestion")

        updated = db.update_review_decision(item_id, "approve", reviewer_id="reviewer-1")
        assert updated["reviewer_decision"] == "approve"
        assert updated["reviewed_by"] == "reviewer-1"
        assert updated["reviewed_at"] is not None

    def test_update_review_with_edit(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        item_id = str(uuid.uuid4())
        db.insert_review_item(item_id, doc_id, "f1", "image", {}, "original suggestion")

        updated = db.update_review_decision(
            item_id, "edit", edit="Better alt text", reviewer_id="reviewer-1"
        )
        assert updated["reviewer_decision"] == "edit"
        assert updated["reviewer_edit"] == "Better alt text"


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class TestUsers:
    def test_create_and_find_user(self, db: Database):
        uid = db.create_user("admin", "Admin User", "admin", "hashed_token_123")
        assert uid is not None

        user = db.get_user_by_token("hashed_token_123")
        assert user is not None
        assert user["username"] == "admin"
        assert user["role"] == "admin"

    def test_find_nonexistent_user(self, db: Database):
        assert db.get_user_by_token("nonexistent") is None

    def test_inactive_user_not_found(self, db: Database):
        uid = db.create_user("test", "Test User", "reviewer", "token_hash")
        db._run("UPDATE users SET active=0 WHERE id=?", (uid,))
        assert db.get_user_by_token("token_hash") is None


# ---------------------------------------------------------------------------
# Change Proposals
# ---------------------------------------------------------------------------


class TestChangeProposals:
    def test_insert_and_get_proposal(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        proposal_id = str(uuid.uuid4())
        evaluation = {"compliance_impact": "positive", "risk": "low"}

        result = db.insert_proposal(
            proposal_id, doc_id, "reviewer-1", "Fix alt text",
            system_evaluation=evaluation, system_recommendation="approve",
        )
        assert result["id"] == proposal_id
        assert result["human_comment"] == "Fix alt text"
        assert result["system_recommendation"] == "approve"

    def test_list_proposals_by_document(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        db.insert_proposal(str(uuid.uuid4()), doc_id, "r1", "comment1", {}, "approve")
        db.insert_proposal(str(uuid.uuid4()), doc_id, "r2", "comment2", {}, "reject")

        proposals = db.list_proposals(document_id=doc_id)
        assert len(proposals) == 2

    def test_list_proposals_by_status(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        p1 = str(uuid.uuid4())
        db.insert_proposal(p1, doc_id, "r1", "c1", {}, "approve")
        db.update_proposal_status(p1, "applied")

        pending = db.list_proposals(status="pending")
        assert len(pending) == 0

        applied = db.list_proposals(status="applied")
        assert len(applied) == 1

    def test_update_proposal_status(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        proposal_id = str(uuid.uuid4())
        db.insert_proposal(proposal_id, doc_id, "r1", "comment", {}, "approve")

        # Use "approved" status which triggers resolved_at in the DB layer
        updated = db.update_proposal_status(
            proposal_id, "approved", resolved_by="admin-1"
        )
        assert updated["status"] == "approved"
        assert updated["resolved_by"] == "admin-1"
        assert updated["resolved_at"] is not None


# ---------------------------------------------------------------------------
# Rules Ledger
# ---------------------------------------------------------------------------


class TestRulesLedger:
    def test_insert_and_get_rules(self, db: Database):
        rule_id = str(uuid.uuid4())
        action = {"type": "add_scope", "value": "col"}
        result = db.insert_rule(rule_id, "table:missing_headers", action, confidence=0.8)
        assert result["id"] == rule_id
        assert result["trigger_pattern"] == "table:missing_headers"

    def test_get_active_rules_empty_by_default(self, db: Database):
        rule_id = str(uuid.uuid4())
        db.insert_rule(rule_id, "table:missing_headers", {"type": "add_scope"})
        # Default status is 'candidate', not 'active'
        active = db.get_active_rules()
        assert len(active) == 0

    def test_promote_rule_to_active(self, db: Database):
        rule_id = str(uuid.uuid4())
        db.insert_rule(rule_id, "image:no_alt", {"type": "set_alt", "value": "placeholder"})
        db.update_rule_status(rule_id, "active")

        active = db.get_active_rules()
        assert len(active) == 1
        assert active[0]["status"] == "active"

    def test_add_validated_doc(self, db: Database):
        rule_id = str(uuid.uuid4())
        db.insert_rule(rule_id, "table:missing_headers", {"type": "add_scope"})

        db.add_validated_doc(rule_id, "doc-1")
        db.add_validated_doc(rule_id, "doc-2")
        db.add_validated_doc(rule_id, "doc-1")  # Duplicate — should not add

        rule = db._one(
            "SELECT * FROM rules_ledger WHERE id=?", (rule_id,), table="rules_ledger"
        )
        docs = rule["validated_on_docs"]
        if isinstance(docs, str):
            docs = json.loads(docs)
        assert len(docs) == 2
        assert "doc-1" in docs
        assert "doc-2" in docs


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_audit_log_on_document_insert(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        logs = db.get_audit_log("document", doc_id)
        assert len(logs) >= 1
        assert logs[0]["action"] == "insert"

    def test_audit_log_on_status_update(self, db: Database, doc_id: str):
        db.insert_document(doc_id, "test.pdf")
        db.update_document_status(doc_id, "extracting")
        logs = db.get_audit_log("document", doc_id)
        assert len(logs) >= 2
        status_logs = [l for l in logs if l["action"] == "status_update"]
        assert len(status_logs) == 1
        assert status_logs[0]["new_value"] == "extracting"

    def test_audit_log_empty_for_unknown_entity(self, db: Database):
        logs = db.get_audit_log("document", "nonexistent")
        assert logs == []
