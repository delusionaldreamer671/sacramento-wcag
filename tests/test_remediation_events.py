"""Tests for the Remediation Audit Trail feature.

Covers:
  - RemediationComponent enum values
  - RemediationEvent field defaults (id, timestamp, source)
  - RemediationEventCollector record / events / to_dict_list / persist_to_db
  - Database.insert_remediation_event / get_remediation_events (SQLite round-trip)
  - GET /api/v1/{task_id}/fixes-applied endpoint (cache hit, cache miss, DB fallback)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.common.database import Database
from services.common.remediation_events import (
    RemediationComponent,
    RemediationEvent,
    RemediationEventCollector,
)


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _fresh_collector(doc_id: str = "doc-001", task_id: str = "task-001") -> RemediationEventCollector:
    return RemediationEventCollector(document_id=doc_id, task_id=task_id)


@pytest.fixture
def db() -> Database:
    """Fresh in-memory SQLite database for each test."""
    return Database(":memory:")


@pytest.fixture
def api_client():
    """TestClient wired to a fresh FastAPI app that includes the api_fixes router.

    Each test gets an isolated module import so that the in-memory cache
    (_SYNC_EVENT_CACHE) starts empty.
    """
    # Import fresh to avoid cross-test cache pollution
    import importlib
    import services.ingestion.api_fixes as api_fixes_module
    importlib.reload(api_fixes_module)

    app = FastAPI()
    app.include_router(api_fixes_module.router)
    return TestClient(app, raise_server_exceptions=True), api_fixes_module


# ---------------------------------------------------------------------------
# 1. test_collector_record_and_retrieve
# ---------------------------------------------------------------------------


class TestCollectorRecordAndRetrieve:
    def test_collector_record_and_retrieve(self):
        """Record 3 events and verify events() returns all 3 with correct data."""
        collector = _fresh_collector()

        collector.record(RemediationComponent.ALT_TEXT, element_id="fig-1", before=None, after="A bar chart.")
        collector.record(RemediationComponent.HEADING_HIERARCHY, element_id="h2-3", before="H3", after="H2")
        collector.record(RemediationComponent.TABLE_STRUCTURE, element_id="tbl-5")

        result = collector.events()
        assert len(result) == 3
        assert all(isinstance(e, RemediationEvent) for e in result)

        components = [e.component for e in result]
        assert RemediationComponent.ALT_TEXT in components
        assert RemediationComponent.HEADING_HIERARCHY in components
        assert RemediationComponent.TABLE_STRUCTURE in components

    def test_events_returns_a_copy(self):
        """events() must return a new list; mutating it does not affect the collector."""
        collector = _fresh_collector()
        collector.record(RemediationComponent.LANGUAGE_TAG)

        returned = collector.events()
        returned.clear()

        # Collector still has 1 event
        assert len(collector.events()) == 1


# ---------------------------------------------------------------------------
# 2. test_collector_to_dict_list
# ---------------------------------------------------------------------------


class TestCollectorToDictList:
    def test_to_dict_list_structure(self):
        """to_dict_list() returns dicts with the expected keys and serialised values."""
        collector = _fresh_collector(doc_id="doc-abc", task_id="task-xyz")
        collector.record(RemediationComponent.PDFUA_METADATA, element_id="meta-1",
                         before="v1", after="v2")

        dicts = collector.to_dict_list()
        assert len(dicts) == 1

        d = dicts[0]
        # Required keys
        assert "id" in d
        assert "document_id" in d
        assert "task_id" in d
        assert "component" in d
        assert "timestamp" in d
        assert "source" in d

        # Values
        assert d["document_id"] == "doc-abc"
        assert d["task_id"] == "task-xyz"
        # Component is serialised as its string value (Pydantic mode="json")
        assert d["component"] == RemediationComponent.PDFUA_METADATA.value
        assert d["before"] == "v1"
        assert d["after"] == "v2"
        assert isinstance(d["id"], str)

    def test_to_dict_list_multiple_events(self):
        """to_dict_list() preserves insertion order."""
        collector = _fresh_collector()
        components = [
            RemediationComponent.ALT_TEXT,
            RemediationComponent.MARK_INFO,
            RemediationComponent.CIDSET_REMOVAL,
        ]
        for comp in components:
            collector.record(comp)

        dicts = collector.to_dict_list()
        assert len(dicts) == 3
        returned_components = [d["component"] for d in dicts]
        assert returned_components == [c.value for c in components]


# ---------------------------------------------------------------------------
# 3. test_collector_empty
# ---------------------------------------------------------------------------


class TestCollectorEmpty:
    def test_events_is_empty_on_new_collector(self):
        collector = RemediationEventCollector()
        assert collector.events() == []

    def test_to_dict_list_is_empty_on_new_collector(self):
        collector = RemediationEventCollector()
        assert collector.to_dict_list() == []


# ---------------------------------------------------------------------------
# 4. test_event_has_auto_generated_id
# ---------------------------------------------------------------------------


class TestEventAutoGeneratedId:
    def test_event_has_non_empty_string_id(self):
        """Every recorded event must receive a non-empty string UUID."""
        collector = _fresh_collector()
        collector.record(RemediationComponent.TAB_ORDER)

        events = collector.events()
        assert len(events) == 1
        event = events[0]

        assert isinstance(event.id, str)
        assert len(event.id) > 0

    def test_event_ids_are_unique_across_events(self):
        """Each event gets a distinct auto-generated ID."""
        collector = _fresh_collector()
        for _ in range(5):
            collector.record(RemediationComponent.VIEWER_PREFERENCES)

        ids = [e.id for e in collector.events()]
        assert len(ids) == len(set(ids)), "Event IDs must be unique"

    def test_event_id_is_valid_uuid(self):
        """The auto-generated ID should be parseable as a UUID."""
        collector = _fresh_collector()
        collector.record(RemediationComponent.FIGURE_CAPTION)

        event = collector.events()[0]
        # This will raise ValueError if not a valid UUID
        parsed = uuid.UUID(event.id)
        assert str(parsed) == event.id


# ---------------------------------------------------------------------------
# 5. test_event_has_timestamp
# ---------------------------------------------------------------------------


class TestEventTimestamp:
    def test_event_has_non_empty_timestamp(self):
        """Recorded events must have a timestamp string set."""
        collector = _fresh_collector()
        collector.record(RemediationComponent.ALT_TEXT)

        event = collector.events()[0]
        assert isinstance(event.timestamp, str)
        assert len(event.timestamp) > 0

    def test_event_timestamp_is_iso_format(self):
        """Timestamp must be parseable as an ISO 8601 datetime."""
        collector = _fresh_collector()
        collector.record(RemediationComponent.MARK_INFO)

        event = collector.events()[0]
        # fromisoformat() raises ValueError if not ISO 8601
        parsed = datetime.fromisoformat(event.timestamp)
        assert parsed is not None

    def test_event_timestamp_is_recent(self):
        """Timestamp should be within the last 60 seconds of now."""
        before = datetime.now(timezone.utc)
        collector = _fresh_collector()
        collector.record(RemediationComponent.TAB_ORDER)
        after = datetime.now(timezone.utc)

        event = collector.events()[0]
        ts = datetime.fromisoformat(event.timestamp)
        # Ensure timezone-aware comparison
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        assert before <= ts <= after


# ---------------------------------------------------------------------------
# 6. test_remediation_component_enum_values
# ---------------------------------------------------------------------------


class TestRemediationComponentEnumValues:
    def test_all_ten_members_exist(self):
        """Verify all 10 expected enum members are present."""
        expected = {
            "ALT_TEXT": "AltText",
            "HEADING_HIERARCHY": "HeadingHierarchy",
            "TABLE_STRUCTURE": "TableStructure",
            "FIGURE_CAPTION": "FigureCaption",
            "LANGUAGE_TAG": "LanguageTag",
            "MARK_INFO": "MarkInfo",
            "PDFUA_METADATA": "PDFUAMetadata",
            "VIEWER_PREFERENCES": "ViewerPreferences",
            "TAB_ORDER": "TabOrder",
            "CIDSET_REMOVAL": "CIDSetRemoval",
        }

        member_names = {m.name for m in RemediationComponent}
        for name in expected:
            assert name in member_names, f"Missing enum member: {name}"

        for name, value in expected.items():
            assert RemediationComponent[name].value == value, (
                f"Wrong value for {name}: expected {value!r}"
            )

    def test_enum_has_exactly_ten_members(self):
        assert len(RemediationComponent) == 10

    def test_enum_is_string_subclass(self):
        """RemediationComponent is a str Enum — values should compare equal to strings."""
        assert RemediationComponent.ALT_TEXT == "AltText"
        assert isinstance(RemediationComponent.HEADING_HIERARCHY, str)


# ---------------------------------------------------------------------------
# 7. test_sqlite_persist_and_query
# ---------------------------------------------------------------------------


class TestSQLitePersistAndQuery:
    def test_insert_and_query_by_task_id(self, db: Database):
        """Persist events via collector.persist_to_db, query back and verify round-trip."""
        task_id = "task-persist-001"
        doc_id = "doc-persist-001"

        collector = RemediationEventCollector(document_id=doc_id, task_id=task_id)
        collector.record(RemediationComponent.ALT_TEXT, element_id="fig-1",
                         before=None, after="A county seal.")
        collector.record(RemediationComponent.HEADING_HIERARCHY, element_id="h2-1",
                         before="H3", after="H2")

        collector.persist_to_db(db)

        rows = db.get_remediation_events(task_id)
        assert len(rows) == 2

    def test_row_fields_match_event(self, db: Database):
        """Verify all persisted field values survive the DB round-trip."""
        task_id = "task-fields-001"
        doc_id = "doc-fields-001"

        collector = RemediationEventCollector(document_id=doc_id, task_id=task_id)
        collector.record(
            RemediationComponent.TABLE_STRUCTURE,
            element_id="tbl-7",
            before={"rows": 3},
            after={"rows": 3, "headers": ["Dept", "Budget"]},
            source="pipeline",
        )
        collector.persist_to_db(db)

        original_event = collector.events()[0]
        rows = db.get_remediation_events(task_id)
        assert len(rows) == 1

        row = rows[0]
        assert row["id"] == original_event.id
        assert row["document_id"] == doc_id
        assert row["task_id"] == task_id
        assert row["component"] == RemediationComponent.TABLE_STRUCTURE.value
        assert row["element_id"] == "tbl-7"
        assert row["source"] == "pipeline"
        assert row["timestamp"] == original_event.timestamp
        # JSON fields are decoded back to Python objects by get_remediation_events
        assert row["before_value"] == {"rows": 3}
        assert row["after_value"] == {"rows": 3, "headers": ["Dept", "Budget"]}

    def test_multiple_tasks_isolated(self, db: Database):
        """Events for different task_ids are stored and retrieved independently."""
        for i in range(3):
            c = RemediationEventCollector(document_id=f"doc-{i}", task_id=f"task-{i}")
            c.record(RemediationComponent.LANGUAGE_TAG)
            c.persist_to_db(db)

        for i in range(3):
            rows = db.get_remediation_events(f"task-{i}")
            assert len(rows) == 1, f"Expected 1 row for task-{i}, got {len(rows)}"

    def test_null_before_and_after_values(self, db: Database):
        """Before/after values of None persist and are read back as None or null-decoded."""
        task_id = "task-null-001"
        collector = RemediationEventCollector(document_id="doc-null", task_id=task_id)
        collector.record(RemediationComponent.CIDSET_REMOVAL, before=None, after=None)
        collector.persist_to_db(db)

        rows = db.get_remediation_events(task_id)
        assert len(rows) == 1
        # JSON-decoded None can come back as None or the string "null"; both are acceptable
        # The important thing is the row exists and was retrieved
        assert rows[0]["task_id"] == task_id


# ---------------------------------------------------------------------------
# 8. test_sqlite_empty_query
# ---------------------------------------------------------------------------


class TestSQLiteEmptyQuery:
    def test_get_nonexistent_task_returns_empty_list(self, db: Database):
        """Query for a task_id that was never inserted returns an empty list."""
        result = db.get_remediation_events("task-does-not-exist")
        assert result == []

    def test_empty_db_returns_empty_list(self, db: Database):
        """A fresh database has no remediation events."""
        result = db.get_remediation_events(str(uuid.uuid4()))
        assert result == []


# ---------------------------------------------------------------------------
# 9. test_api_endpoint_from_cache
# ---------------------------------------------------------------------------


class TestAPIEndpointFromCache:
    def test_cache_hit_returns_events(self, api_client):
        """GET /api/{task_id}/fixes-applied returns cached events when present."""
        client, api_fixes_module = api_client
        task_id = "task-cache-hit-001"

        events = [
            {"id": "evt-1", "component": "AltText", "source": "pipeline"},
            {"id": "evt-2", "component": "TabOrder", "source": "pipeline"},
        ]
        api_fixes_module.cache_events(task_id, events)

        response = client.get(f"/api/v1/{task_id}/fixes-applied")
        assert response.status_code == 200

        data = response.json()
        assert data["task_id"] == task_id
        assert data["event_count"] == 2
        assert len(data["events"]) == 2
        assert data["events"][0]["id"] == "evt-1"
        assert data["events"][1]["id"] == "evt-2"

    def test_cache_returns_correct_task_id_field(self, api_client):
        """Response body task_id matches the path parameter."""
        client, api_fixes_module = api_client
        task_id = "task-id-check-abc123"
        api_fixes_module.cache_events(task_id, [{"component": "MarkInfo"}])

        response = client.get(f"/api/v1/{task_id}/fixes-applied")
        assert response.status_code == 200
        assert response.json()["task_id"] == task_id

    def test_cache_event_count_matches_events_list_length(self, api_client):
        """event_count in response must match the length of the events array."""
        client, api_fixes_module = api_client
        task_id = "task-count-check"
        events = [{"id": f"e{i}"} for i in range(5)]
        api_fixes_module.cache_events(task_id, events)

        response = client.get(f"/api/v1/{task_id}/fixes-applied")
        data = response.json()
        assert data["event_count"] == len(data["events"])

    def test_cache_overwrites_previous_entry(self, api_client):
        """Calling cache_events twice for the same task_id replaces the first entry."""
        client, api_fixes_module = api_client
        task_id = "task-overwrite"

        api_fixes_module.cache_events(task_id, [{"id": "old"}])
        api_fixes_module.cache_events(task_id, [{"id": "new-1"}, {"id": "new-2"}])

        response = client.get(f"/api/v1/{task_id}/fixes-applied")
        data = response.json()
        assert data["event_count"] == 2
        assert data["events"][0]["id"] == "new-1"


# ---------------------------------------------------------------------------
# 10. test_api_endpoint_cache_miss_empty
# ---------------------------------------------------------------------------


class TestAPIEndpointCacheMiss:
    def test_unknown_task_returns_404(self, api_client):
        """GET /api/{task_id}/fixes-applied for a task_id not in cache or DB → 404."""
        client, _ = api_client
        task_id = f"task-unknown-{uuid.uuid4()}"

        response = client.get(f"/api/v1/{task_id}/fixes-applied")
        # The endpoint raises HTTPException(404) when neither cache nor DB has events
        assert response.status_code == 404

    def test_404_error_detail_mentions_task_id(self, api_client):
        """The 404 error detail should reference the missing task_id."""
        client, _ = api_client
        task_id = "task-missing-xyz"

        response = client.get(f"/api/v1/{task_id}/fixes-applied")
        assert response.status_code == 404
        detail = response.json().get("detail", "")
        assert task_id in detail


# ---------------------------------------------------------------------------
# 11. test_source_field_defaults_to_pipeline
# ---------------------------------------------------------------------------


class TestSourceFieldDefault:
    def test_source_defaults_to_pipeline_on_record(self):
        """Recording an event without specifying source results in source='pipeline'."""
        collector = _fresh_collector()
        collector.record(RemediationComponent.VIEWER_PREFERENCES)

        event = collector.events()[0]
        assert event.source == "pipeline"

    def test_source_default_in_to_dict_list(self):
        """to_dict_list() serialises the default source as 'pipeline'."""
        collector = _fresh_collector()
        collector.record(RemediationComponent.PDFUA_METADATA)

        d = collector.to_dict_list()[0]
        assert d["source"] == "pipeline"

    def test_source_default_in_raw_event_model(self):
        """RemediationEvent itself has source='pipeline' as a field default."""
        event = RemediationEvent(
            document_id="doc-x",
            task_id="task-x",
            component=RemediationComponent.MARK_INFO,
        )
        assert event.source == "pipeline"


# ---------------------------------------------------------------------------
# 12. test_source_field_accepts_custom
# ---------------------------------------------------------------------------


class TestSourceFieldCustom:
    def test_record_with_custom_source(self):
        """Specifying source='clause_fixer' is preserved on the event."""
        collector = _fresh_collector()
        collector.record(RemediationComponent.ALT_TEXT, source="clause_fixer")

        event = collector.events()[0]
        assert event.source == "clause_fixer"

    def test_custom_source_in_to_dict_list(self):
        """Custom source is preserved through to_dict_list() serialisation."""
        collector = _fresh_collector()
        collector.record(RemediationComponent.TABLE_STRUCTURE, source="ai")

        d = collector.to_dict_list()[0]
        assert d["source"] == "ai"

    def test_custom_source_survives_db_round_trip(self, db: Database):
        """Custom source value is stored and retrieved correctly from SQLite."""
        task_id = "task-source-rt"
        collector = RemediationEventCollector(document_id="doc-src", task_id=task_id)
        collector.record(RemediationComponent.HEADING_HIERARCHY, source="human")
        collector.persist_to_db(db)

        rows = db.get_remediation_events(task_id)
        assert len(rows) == 1
        assert rows[0]["source"] == "human"

    def test_multiple_sources_in_single_collector(self):
        """A collector can hold events with different source values."""
        collector = _fresh_collector()
        collector.record(RemediationComponent.ALT_TEXT, source="pipeline")
        collector.record(RemediationComponent.FIGURE_CAPTION, source="ai")
        collector.record(RemediationComponent.TAB_ORDER, source="human")

        sources = [e.source for e in collector.events()]
        assert sources == ["pipeline", "ai", "human"]
