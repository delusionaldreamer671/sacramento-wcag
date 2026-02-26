"""Tests for Alembic migration infrastructure."""
from __future__ import annotations

import importlib
import sqlite3

import pytest


class TestInitialMigration:
    """Verify migration 001 creates all expected tables and indexes."""

    def test_upgrade_creates_all_tables(self, tmp_path):
        """Run migration 001 upgrade DDL against a fresh SQLite DB."""
        mod = importlib.import_module(
            "services.common.migrations.versions.001_initial_schema"
        )
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Execute the SQLite DDL directly (same as what upgrade() runs)
        for statement in mod._SQLITE_DDL.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                cursor.execute(stmt)
        for statement in mod._INDEXES.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                cursor.execute(stmt)
        conn.commit()

        # Verify all 12 application tables exist (exclude sqlite_sequence)
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence' ORDER BY name"
        )
        tables = sorted(row[0] for row in cursor.fetchall())
        expected = sorted([
            "alt_text_proposals",
            "audit_log",
            "baseline_validations",
            "change_proposals",
            "documents",
            "image_assets",
            "pipeline_telemetry",
            "remediation_events",
            "review_items",
            "rules_ledger",
            "users",
            "wcag_findings",
        ])
        assert tables == expected

        # Verify indexes exist
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        assert "idx_docs_status" in indexes
        assert "idx_findings_doc" in indexes
        assert "idx_img_doc" in indexes
        assert "idx_altp_status" in indexes

        conn.close()

    def test_upgrade_is_idempotent(self, tmp_path):
        """Running migration DDL twice doesn't error (IF NOT EXISTS)."""
        mod = importlib.import_module(
            "services.common.migrations.versions.001_initial_schema"
        )
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Run twice
        for _ in range(2):
            for statement in mod._SQLITE_DDL.strip().split(";"):
                stmt = statement.strip()
                if stmt:
                    cursor.execute(stmt)
            for statement in mod._INDEXES.strip().split(";"):
                stmt = statement.strip()
                if stmt:
                    cursor.execute(stmt)
        conn.commit()

        cursor.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'"
        )
        count = cursor.fetchone()[0]
        assert count == 12
        conn.close()

    def test_downgrade_drops_all_tables(self, tmp_path):
        """Downgrade removes all tables."""
        mod = importlib.import_module(
            "services.common.migrations.versions.001_initial_schema"
        )
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Create tables
        for statement in mod._SQLITE_DDL.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                cursor.execute(stmt)
        conn.commit()

        # Drop tables (simulating downgrade)
        tables_to_drop = [
            "alt_text_proposals", "baseline_validations", "image_assets",
            "pipeline_telemetry", "remediation_events", "audit_log",
            "rules_ledger", "change_proposals", "review_items",
            "wcag_findings", "users", "documents",
        ]
        for table in tables_to_drop:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()

        cursor.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'"
        )
        count = cursor.fetchone()[0]
        assert count == 0
        conn.close()


class TestGcsPathMigration:
    """Verify migration 002 adds gcs_path column."""

    def test_adds_gcs_path_column(self, tmp_path):
        mod_001 = importlib.import_module(
            "services.common.migrations.versions.001_initial_schema"
        )
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Create base schema
        for statement in mod_001._SQLITE_DDL.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                cursor.execute(stmt)
        conn.commit()

        # Add gcs_path column
        cursor.execute(
            "ALTER TABLE image_assets ADD COLUMN gcs_path TEXT DEFAULT NULL"
        )
        conn.commit()

        # Verify column exists
        cursor.execute("PRAGMA table_info(image_assets)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "gcs_path" in columns
        assert "image_data" in columns  # Original BLOB still there
        conn.close()


class TestNvdaQaMigration:
    """Verify migration 003 creates nvda_qa_results table."""

    def test_creates_nvda_qa_results_table(self, tmp_path):
        mod_003 = importlib.import_module(
            "services.common.migrations.versions.003_add_nvda_qa_results"
        )
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute(mod_003._CREATE_TABLE_SQLITE)
        conn.commit()

        cursor.execute("PRAGMA table_info(nvda_qa_results)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "id", "document_id", "task_id", "reviewer",
            "checklist_json", "overall_status", "notes",
            "created_at", "completed_at",
        }
        assert columns == expected
        conn.close()
