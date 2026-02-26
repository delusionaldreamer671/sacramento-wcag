"""Tests for db_backend.py — SQLiteBackend, PostgresBackend pooling, and utilities.

All PostgresBackend tests use mocked psycopg connections so no real Postgres
server is required.  The pool internals (_PostgresConnectionPool) are tested
with real mock objects that simulate connection behaviour.

Coverage:
  - _translate_params: ? → %s substitution
  - SQLiteBackend: full method coverage with :memory: database
  - _PostgresConnectionPool: acquire/release lifecycle, overflow, broken-conn
  - PostgresBackend: thread-local pinning, all protocol methods
  - create_backend: factory routing and error cases
  - PostgresBackend method parity: every method on SQLiteBackend exists on PostgresBackend
"""

from __future__ import annotations

import queue
import threading
import types
import uuid
from unittest.mock import MagicMock, patch, call

import pytest

from services.common.db_backend import (
    SQLiteBackend,
    PostgresBackend,
    _PostgresConnectionPool,
    _translate_params,
    create_backend,
    DatabaseBackend,
)


# ---------------------------------------------------------------------------
# _translate_params
# ---------------------------------------------------------------------------


class TestTranslateParams:
    def test_single_placeholder(self):
        assert _translate_params("SELECT * FROM t WHERE id=?") == \
               "SELECT * FROM t WHERE id=%s"

    def test_multiple_placeholders(self):
        assert _translate_params("INSERT INTO t (a,b) VALUES (?,?)") == \
               "INSERT INTO t (a,b) VALUES (%s,%s)"

    def test_no_placeholders(self):
        sql = "SELECT * FROM documents"
        assert _translate_params(sql) == sql

    def test_does_not_alter_percent_signs(self):
        # Existing %s in the SQL must survive (although unlikely in this
        # codebase, translate_params should only substitute ? characters)
        sql = "SELECT * FROM t WHERE val=%s"
        assert _translate_params(sql) == sql


# ---------------------------------------------------------------------------
# SQLiteBackend
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_backend():
    """In-memory SQLiteBackend with a minimal test table."""
    backend = SQLiteBackend(":memory:")
    backend.execute_ddl(
        "CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, name TEXT NOT NULL)"
    )
    return backend


class TestSQLiteBackend:
    def test_backend_type(self, sqlite_backend):
        assert sqlite_backend.backend_type == "sqlite"

    def test_execute_and_commit(self, sqlite_backend):
        sqlite_backend.execute("INSERT INTO items (id, name) VALUES (?, ?)", ("1", "alpha"))
        sqlite_backend.commit()
        row = sqlite_backend.fetchone("SELECT * FROM items WHERE id=?", ("1",))
        assert row is not None
        assert row["name"] == "alpha"

    def test_executemany(self, sqlite_backend):
        pairs = [("a", "apple"), ("b", "banana"), ("c", "cherry")]
        sqlite_backend.executemany("INSERT INTO items (id, name) VALUES (?, ?)", pairs)
        rows = sqlite_backend.fetchall("SELECT * FROM items ORDER BY id")
        assert len(rows) == 3
        assert rows[0]["name"] == "apple"

    def test_fetchone_returns_none_for_missing(self, sqlite_backend):
        result = sqlite_backend.fetchone("SELECT * FROM items WHERE id=?", ("missing",))
        assert result is None

    def test_fetchall_returns_empty_list(self, sqlite_backend):
        results = sqlite_backend.fetchall("SELECT * FROM items")
        assert results == []

    def test_execute_returns_cursor_with_rowcount(self, sqlite_backend):
        sqlite_backend.execute("INSERT INTO items (id, name) VALUES (?, ?)", ("x", "xray"))
        sqlite_backend.commit()
        cur = sqlite_backend.execute("UPDATE items SET name=? WHERE id=?", ("xenon", "x"))
        assert cur.rowcount == 1
        sqlite_backend.commit()

    def test_rowcount_zero_for_no_match(self, sqlite_backend):
        cur = sqlite_backend.execute(
            "UPDATE items SET name=? WHERE id=?", ("newname", "nonexistent")
        )
        sqlite_backend.commit()
        assert cur.rowcount == 0

    def test_execute_ddl_creates_table(self):
        backend = SQLiteBackend(":memory:")
        backend.execute_ddl("CREATE TABLE foo (id TEXT PRIMARY KEY)")
        backend.execute("INSERT INTO foo (id) VALUES (?)", ("bar",))
        backend.commit()
        row = backend.fetchone("SELECT id FROM foo WHERE id=?", ("bar",))
        assert row == {"id": "bar"}

    def test_execute_pragma_no_error(self, sqlite_backend):
        # Pragmas must not raise
        sqlite_backend.execute_pragma("PRAGMA journal_mode=WAL;")
        sqlite_backend.execute_pragma("PRAGMA foreign_keys=ON;")

    def test_thread_safety(self, sqlite_backend):
        """Concurrent inserts from multiple threads must not corrupt data."""
        errors = []

        def insert_row(n: int):
            try:
                sqlite_backend.execute(
                    "INSERT INTO items (id, name) VALUES (?, ?)", (str(n), f"item-{n}")
                )
                sqlite_backend.commit()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=insert_row, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        rows = sqlite_backend.fetchall("SELECT * FROM items")
        assert len(rows) == 20

    def test_implements_backend_protocol(self, sqlite_backend):
        assert isinstance(sqlite_backend, DatabaseBackend)


# ---------------------------------------------------------------------------
# _PostgresConnectionPool (mocked psycopg connections)
# ---------------------------------------------------------------------------


def _make_mock_psycopg():
    """Return a mock psycopg module with connect() that returns mock connections."""
    mock_conn = MagicMock()
    mock_conn.info.transaction_status = 0  # IDLE — no open transaction
    mock_conn.row_factory = None

    mock_psycopg = MagicMock()
    mock_psycopg.connect.return_value = mock_conn

    mock_dict_row = MagicMock()

    return mock_psycopg, mock_dict_row, mock_conn


class TestPostgresConnectionPool:
    """Unit tests for _PostgresConnectionPool using mocked psycopg."""

    def _make_pool(self, minconn=1, maxconn=3):
        """Create a pool with mocked psycopg.connect."""
        mock_psycopg, mock_dict_row, _ = _make_mock_psycopg()
        # Each connect() call returns a unique mock connection
        mock_psycopg.connect.side_effect = [MagicMock() for _ in range(maxconn + 5)]

        pool = _PostgresConnectionPool.__new__(_PostgresConnectionPool)
        pool._dsn = "postgresql://test/db"
        pool._maxconn = maxconn
        pool._timeout = 1.0  # Short timeout for tests
        pool._lock = threading.Lock()
        pool._all_conns = []
        pool._pool = queue.Queue()
        pool._psycopg = mock_psycopg
        pool._dict_row = mock_dict_row

        for _ in range(minconn):
            conn = pool._make_conn()
            pool._all_conns.append(conn)
            pool._pool.put(conn)

        return pool

    def test_acquire_returns_connection(self):
        pool = self._make_pool(minconn=2)
        conn = pool.acquire()
        assert conn is not None

    def test_acquire_release_cycle(self):
        pool = self._make_pool(minconn=1)
        conn = pool.acquire()
        assert pool._pool.qsize() == 0
        pool.release(conn)
        assert pool._pool.qsize() == 1

    def test_acquire_creates_new_under_maxconn(self):
        pool = self._make_pool(minconn=1, maxconn=3)
        # Drain the initial connection
        conn1 = pool.acquire()
        # Pool should create a new one (we are under maxconn=3)
        conn2 = pool.acquire()
        assert conn1 is not conn2
        assert len(pool._all_conns) == 2

    def test_acquire_blocks_and_raises_when_pool_exhausted(self):
        pool = self._make_pool(minconn=2, maxconn=2)
        # Drain all connections
        conn1 = pool.acquire()
        conn2 = pool.acquire()
        # Now the pool is full and exhausted — timeout=1s should raise quickly
        with pytest.raises(RuntimeError, match="connection pool exhausted"):
            pool.acquire()

    def test_release_rolls_back_open_transaction(self):
        pool = self._make_pool(minconn=1)
        conn = pool.acquire()
        # Simulate open transaction
        conn.info.transaction_status = 1  # INTRANS
        pool.release(conn)
        conn.rollback.assert_called_once()

    def test_release_discards_broken_connection(self):
        pool = self._make_pool(minconn=1)
        conn = pool.acquire()
        # Make rollback raise (simulates broken connection)
        conn.info.transaction_status = 1
        conn.rollback.side_effect = Exception("connection reset")
        pool.release(conn)
        # Broken connection must be removed from _all_conns
        assert conn not in pool._all_conns

    def test_connection_context_manager_releases_on_exit(self):
        pool = self._make_pool(minconn=2)
        initial_size = pool._pool.qsize()
        with pool.connection() as conn:
            assert conn is not None
            # One connection is checked out
            assert pool._pool.qsize() == initial_size - 1
        # Connection released back
        assert pool._pool.qsize() == initial_size

    def test_redact_dsn(self):
        redacted = _PostgresConnectionPool._redact_dsn(
            "postgresql://user:s3cr3t@localhost:5432/db"
        )
        assert "s3cr3t" not in redacted
        assert "***" in redacted

    def test_redact_dsn_without_password(self):
        dsn = "postgresql://localhost/db"
        assert _PostgresConnectionPool._redact_dsn(dsn) == dsn


# ---------------------------------------------------------------------------
# PostgresBackend (mocked pool)
# ---------------------------------------------------------------------------


def _make_postgres_backend(minconn=1, maxconn=3):
    """Build a PostgresBackend with a fully mocked pool."""
    backend = PostgresBackend.__new__(PostgresBackend)
    backend._local = threading.local()

    # Build a real-ish pool but stub _make_conn to return mock connections
    pool = _PostgresConnectionPool.__new__(_PostgresConnectionPool)
    pool._dsn = "postgresql://test/db"
    pool._maxconn = maxconn
    pool._timeout = 1.0
    pool._lock = threading.Lock()
    pool._all_conns = []
    pool._pool = queue.Queue()

    def _make_fresh_mock_conn():
        conn = MagicMock()
        conn.info.transaction_status = 0
        return conn

    pool._psycopg = MagicMock()
    pool._dict_row = MagicMock()
    pool._make_conn = _make_fresh_mock_conn

    for _ in range(minconn):
        c = pool._make_conn()
        pool._all_conns.append(c)
        pool._pool.put(c)

    backend._pool = pool
    return backend


class TestPostgresBackendProtocol:
    """Tests for PostgresBackend protocol methods using a mocked pool."""

    def test_backend_type(self):
        b = _make_postgres_backend()
        assert b.backend_type == "postgres"

    def test_execute_pins_thread_local_connection(self):
        b = _make_postgres_backend()
        cur = b.execute("SELECT 1")
        # Thread-local conn must be set after execute()
        assert b._local.conn is not None
        # Commit releases it
        b.commit()
        assert b._local.conn is None

    def test_execute_returns_cursor(self):
        b = _make_postgres_backend()
        cur = b.execute("INSERT INTO t (id) VALUES (%s)", ("x",))
        b.commit()
        assert cur is not None

    def test_commit_calls_conn_commit(self):
        b = _make_postgres_backend()
        b.execute("INSERT INTO t VALUES (%s)", ("a",))
        conn = b._local.conn
        b.commit()
        conn.commit.assert_called_once()

    def test_commit_without_prior_execute_is_noop(self):
        b = _make_postgres_backend()
        # Should not raise even if no connection was pinned
        b.commit()

    def test_commit_releases_connection_to_pool(self):
        b = _make_postgres_backend()
        pool_size_before = b._pool._pool.qsize()
        b.execute("SELECT 1")
        assert b._pool._pool.qsize() == pool_size_before - 1
        b.commit()
        assert b._pool._pool.qsize() == pool_size_before

    def test_execute_error_releases_connection(self):
        b = _make_postgres_backend()
        # Grab the connection and make it raise on execute
        conn = b._pool._pool.queue[0] if hasattr(b._pool._pool, "queue") else \
               list(b._pool._all_conns)[0]
        pool_size_before = b._pool._pool.qsize()

        # Patch _get_conn to return a conn that raises
        failing_conn = MagicMock()
        failing_conn.execute.side_effect = RuntimeError("db error")
        failing_conn.info.transaction_status = 0
        b._local.conn = None  # ensure fresh

        original_acquire = b._pool.acquire
        b._pool.acquire = MagicMock(return_value=failing_conn)

        with pytest.raises(RuntimeError, match="db error"):
            b.execute("SELECT 1")

        # Connection must be released (rollback path)
        b._pool.acquire = original_acquire
        # _local.conn must be None after error
        assert b._local.conn is None

    def test_fetchone_uses_pool_connection_not_thread_local(self):
        b = _make_postgres_backend()
        # fetchone must NOT pin the thread-local conn
        mock_conn = MagicMock()
        mock_conn.info.transaction_status = 0
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {"id": "abc"}
        mock_conn.execute.return_value = mock_cur

        b._pool.acquire = MagicMock(return_value=mock_conn)
        b._pool.release = MagicMock()

        result = b.fetchone("SELECT * FROM t WHERE id=%s", ("abc",))
        assert result == {"id": "abc"}
        # fetchone must NOT pin the thread-local connection — either the
        # attribute is absent (never set) or it is None.
        assert getattr(b._local, "conn", None) is None
        b._pool.release.assert_called_once_with(mock_conn)

    def test_fetchall_returns_list_of_dicts(self):
        b = _make_postgres_backend()
        mock_conn = MagicMock()
        mock_conn.info.transaction_status = 0
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]
        mock_conn.execute.return_value = mock_cur

        b._pool.acquire = MagicMock(return_value=mock_conn)
        b._pool.release = MagicMock()

        rows = b.fetchall("SELECT * FROM t")
        assert len(rows) == 2
        assert rows[0]["name"] == "a"

    def test_executemany_commits_and_releases(self):
        b = _make_postgres_backend()
        mock_conn = MagicMock()
        mock_conn.info.transaction_status = 0

        original_get = b._get_conn
        b._get_conn = MagicMock(return_value=mock_conn)
        original_put = b._put_conn
        b._put_conn = MagicMock()

        b.executemany("INSERT INTO t VALUES (%s)", [("a",), ("b",)])

        mock_conn.executemany.assert_called_once()
        mock_conn.commit.assert_called_once()
        b._put_conn.assert_called_once()

        b._get_conn = original_get
        b._put_conn = original_put

    def test_execute_ddl_runs_all_statements(self):
        b = _make_postgres_backend()
        mock_conn = MagicMock()
        mock_conn.info.transaction_status = 0

        b._pool.acquire = MagicMock(return_value=mock_conn)
        b._pool.release = MagicMock()

        b.execute_ddl("CREATE TABLE a (id TEXT); CREATE TABLE b (id TEXT)")
        assert mock_conn.execute.call_count == 2
        mock_conn.commit.assert_called_once()

    def test_execute_ddl_skips_empty_statements(self):
        b = _make_postgres_backend()
        mock_conn = MagicMock()
        mock_conn.info.transaction_status = 0

        b._pool.acquire = MagicMock(return_value=mock_conn)
        b._pool.release = MagicMock()

        # Two real statements separated by extra semicolons → 2 execute calls
        b.execute_ddl("CREATE TABLE a (id TEXT);;;CREATE TABLE b (id TEXT);")
        assert mock_conn.execute.call_count == 2

    def test_execute_pragma_is_noop(self):
        b = _make_postgres_backend()
        # Must not raise and must not touch the pool
        b._pool.acquire = MagicMock(side_effect=AssertionError("should not acquire"))
        b.execute_pragma("PRAGMA journal_mode=WAL;")

    def test_upsert_image_calls_on_conflict(self):
        b = _make_postgres_backend()
        mock_conn = MagicMock()
        mock_conn.info.transaction_status = 0

        b._pool.acquire = MagicMock(return_value=mock_conn)
        b._pool.release = MagicMock()

        b.upsert_image("image_id, document_id", ("img1", "doc1"))

        call_args = mock_conn.execute.call_args
        sql = call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "image_assets" in sql
        mock_conn.commit.assert_called_once()

    def test_get_conn_and_put_conn_helpers(self):
        b = _make_postgres_backend()
        conn = b._get_conn()
        assert conn is not None
        assert b._local.conn is conn  # pinned
        b._put_conn()
        assert b._local.conn is None  # released

    def test_pool_get_conn_and_put_conn_explicit(self):
        b = _make_postgres_backend()
        conn = b._pool_get_conn()
        assert conn is not None
        b._pool_put_conn(conn)

    def test_close_calls_pool_close_all(self):
        b = _make_postgres_backend()
        b._pool.close_all = MagicMock()
        b.close()
        b._pool.close_all.assert_called_once()


# ---------------------------------------------------------------------------
# Method parity: every method on SQLiteBackend must exist on PostgresBackend
# ---------------------------------------------------------------------------


class TestMethodParity:
    """Ensure PostgresBackend implements all methods that SQLiteBackend has.

    This guards against protocol drift — if a new method is added to
    SQLiteBackend, this test will fail until it is added to PostgresBackend.
    """

    _IGNORED = frozenset({"__init__", "__class__"})

    def _public_methods(self, cls):
        return {
            name for name in dir(cls)
            if not name.startswith("__") and callable(getattr(cls, name))
        }

    def test_postgres_has_all_sqlite_methods(self):
        sqlite_methods = self._public_methods(SQLiteBackend)
        postgres_methods = self._public_methods(PostgresBackend)
        missing = sqlite_methods - postgres_methods
        assert missing == set(), (
            f"PostgresBackend is missing these methods from SQLiteBackend: {sorted(missing)}"
        )

    def test_protocol_methods_present_on_both(self):
        """Every method defined in DatabaseBackend protocol must be on both classes."""
        protocol_methods = {
            "execute", "executemany", "fetchone", "fetchall",
            "commit", "execute_ddl", "execute_pragma", "backend_type",
        }
        for method in protocol_methods:
            assert hasattr(SQLiteBackend, method), \
                f"SQLiteBackend missing protocol method: {method}"
            assert hasattr(PostgresBackend, method), \
                f"PostgresBackend missing protocol method: {method}"


# ---------------------------------------------------------------------------
# create_backend factory
# ---------------------------------------------------------------------------


class TestCreateBackend:
    def test_default_creates_sqlite(self):
        backend = create_backend()
        assert isinstance(backend, SQLiteBackend)
        assert backend.backend_type == "sqlite"

    def test_sqlite_explicit(self):
        backend = create_backend("sqlite", db_path=":memory:")
        assert isinstance(backend, SQLiteBackend)

    def test_postgres_requires_url(self):
        with pytest.raises(ValueError, match="postgres_url is required"):
            create_backend("postgres", postgres_url="")

    def test_postgres_with_url_calls_constructor(self):
        """create_backend('postgres', ...) must return PostgresBackend.

        We patch _PostgresConnectionPool to avoid a real DB connection.
        """
        with patch.object(
            _PostgresConnectionPool, "__init__", return_value=None
        ) as mock_init, patch.object(
            _PostgresConnectionPool, "acquire", side_effect=RuntimeError("no real db")
        ):
            # __init__ must not call acquire(), so this should succeed
            mock_pool = MagicMock()
            mock_pool.acquire.return_value = MagicMock()
            mock_pool.release = MagicMock()

            with patch(
                "services.common.db_backend._PostgresConnectionPool",
                return_value=mock_pool,
            ) as MockPool:
                backend = create_backend("postgres", postgres_url="postgresql://h/db")
                assert isinstance(backend, PostgresBackend)
                assert backend.backend_type == "postgres"


# ---------------------------------------------------------------------------
# get_db() Cloud Run warning (database.py)
# ---------------------------------------------------------------------------


class TestGetDbCloudRunWarning:
    """Verify the CRITICAL-3.1 startup warning is emitted for SQLite on Cloud Run."""

    def test_warning_emitted_when_k_service_set_and_sqlite(self, monkeypatch):
        import importlib
        import services.common.database as db_module

        # Reset singleton so get_db() will run initialisation
        original_instance = db_module._instance
        db_module._instance = None

        monkeypatch.setenv("K_SERVICE", "sacto-wcag-api")

        # Mock settings to return sqlite
        mock_settings = MagicMock()
        mock_settings.db_backend = "sqlite"
        mock_settings.postgres_url = ""

        with patch("services.common.database.Database.__init__", return_value=None), \
             patch("services.common.config.settings", mock_settings), \
             patch("services.common.database.logger") as mock_logger:

            db_module._instance = None
            try:
                db_module.get_db(":memory:")
            except Exception:
                pass  # Database.__init__ is mocked to return None — that's OK

            # Check CRITICAL warning was logged
            critical_calls = [
                str(c) for c in mock_logger.critical.call_args_list
            ]
            assert any("CRITICAL-3.1" in msg for msg in critical_calls), (
                f"Expected CRITICAL-3.1 warning; got: {critical_calls}"
            )

        # Restore singleton
        db_module._instance = original_instance

    def test_no_warning_when_k_service_not_set(self, monkeypatch):
        import services.common.database as db_module

        original_instance = db_module._instance
        db_module._instance = None
        monkeypatch.delenv("K_SERVICE", raising=False)

        mock_settings = MagicMock()
        mock_settings.db_backend = "sqlite"
        mock_settings.postgres_url = ""

        with patch("services.common.database.Database.__init__", return_value=None), \
             patch("services.common.config.settings", mock_settings), \
             patch("services.common.database.logger") as mock_logger:

            db_module._instance = None
            try:
                db_module.get_db(":memory:")
            except Exception:
                pass

            critical_calls = mock_logger.critical.call_args_list
            ephemeral_warnings = [
                c for c in critical_calls if "CRITICAL-3.1" in str(c)
            ]
            assert ephemeral_warnings == []

        db_module._instance = original_instance

    def test_no_warning_when_postgres_configured(self, monkeypatch):
        import services.common.database as db_module

        original_instance = db_module._instance
        db_module._instance = None
        monkeypatch.setenv("K_SERVICE", "sacto-wcag-api")

        mock_settings = MagicMock()
        mock_settings.db_backend = "postgres"
        mock_settings.postgres_url = "postgresql://host/db"

        mock_pool = MagicMock()
        mock_pool.acquire.return_value = MagicMock()
        mock_pool.release = MagicMock()

        with patch("services.common.database.Database.__init__", return_value=None), \
             patch("services.common.config.settings", mock_settings), \
             patch(
                 "services.common.db_backend._PostgresConnectionPool",
                 return_value=mock_pool,
             ), \
             patch("services.common.database.logger") as mock_logger:

            db_module._instance = None
            try:
                db_module.get_db(":memory:")
            except Exception:
                pass

            critical_calls = mock_logger.critical.call_args_list
            ephemeral_warnings = [
                c for c in critical_calls if "CRITICAL-3.1" in str(c)
            ]
            assert ephemeral_warnings == []

        db_module._instance = original_instance
