"""Database backend abstraction layer.

Provides a protocol-based interface for swapping between SQLite and PostgreSQL
without changing the SQL queries in database.py. The backend handles:
- Connection creation and lifecycle
- Parameter placeholder translation (? → %s for Postgres)
- Row factory (dict rows)
- PRAGMA handling (SQLite-only)
- DDL execution
- Connection pooling (PostgresBackend — ThreadedConnectionPool via queue.Queue)

Usage:
    from services.common.db_backend import create_backend

    backend = create_backend("sqlite", db_path="wcag_pipeline.db")
    # or
    backend = create_backend("postgres", postgres_url="postgresql://...")
"""

from __future__ import annotations

import contextlib
import logging
import queue
import re
import threading
from typing import Any, Generator, Protocol, runtime_checkable

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
    """SQLite backend — wraps sqlite3 connection.

    Thread safety: A threading.Lock serialises all database operations.
    SQLite's WAL mode allows concurrent reads at the file level, but the
    single connection object is not safe to use from multiple threads
    simultaneously without explicit locking.
    """

    def __init__(self, db_path: str = "wcag_pipeline.db") -> None:
        import sqlite3
        import threading
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def execute(self, sql: str, params: tuple = ()) -> Any:
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        with self._lock:
            self._conn.executemany(sql, params_list)

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def execute_ddl(self, ddl: str) -> None:
        with self._lock:
            self._conn.executescript(ddl)
            self._conn.commit()

    def execute_pragma(self, pragma: str) -> None:
        with self._lock:
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


class _PostgresConnectionPool:
    """Thread-safe connection pool for psycopg v3 (psycopg[binary]).

    Uses a bounded queue.Queue to hold idle connections.  Each call to
    ``acquire()`` removes a connection from the pool (blocking up to
    ``timeout`` seconds if all connections are in use); ``release()``
    returns it.  The context manager ``connection()`` handles acquire /
    release automatically.

    Pool parameters:
        minconn -- connections created eagerly at startup (default 2)
        maxconn -- hard cap on total connections (default 10)
        timeout -- seconds to wait for a free connection before raising
                   RuntimeError (default 30)
    """

    def __init__(self, dsn: str, minconn: int = 2, maxconn: int = 10,
                 timeout: float = 30.0) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required for PostgreSQL backend. "
                "Install with: pip install 'psycopg[binary]>=3.1.0'"
            ) from exc

        self._dsn = dsn
        self._maxconn = maxconn
        self._timeout = timeout
        self._lock = threading.Lock()
        self._all_conns: list[Any] = []
        self._pool: queue.Queue[Any] = queue.Queue()

        # Import stored for _make_conn
        self._psycopg = psycopg
        self._dict_row = dict_row

        # Eagerly open minconn connections
        for _ in range(minconn):
            conn = self._make_conn()
            self._all_conns.append(conn)
            self._pool.put(conn)

        logger.info(
            "PostgresBackend: pool initialised (minconn=%d, maxconn=%d, dsn=%s)",
            minconn, maxconn, self._redact_dsn(dsn),
        )

    @staticmethod
    def _redact_dsn(dsn: str) -> str:
        """Remove password from DSN for safe logging."""
        return re.sub(r":[^:@/]+@", ":***@", dsn)

    def _make_conn(self) -> Any:
        conn = self._psycopg.connect(self._dsn, autocommit=False)
        conn.row_factory = self._dict_row
        return conn

    def acquire(self) -> Any:
        """Get a connection from the pool, creating one if under maxconn."""
        # Fast path: try to get without blocking
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            pass

        # Slow path: can we create a new connection?
        with self._lock:
            if len(self._all_conns) < self._maxconn:
                conn = self._make_conn()
                self._all_conns.append(conn)
                logger.debug(
                    "PostgresBackend: opened new connection (%d/%d)",
                    len(self._all_conns), self._maxconn,
                )
                return conn

        # Pool exhausted — wait for one to become available
        try:
            return self._pool.get(timeout=self._timeout)
        except queue.Empty:
            raise RuntimeError(
                f"PostgresBackend: connection pool exhausted after {self._timeout}s "
                f"(maxconn={self._maxconn}). Increase maxconn or reduce concurrency."
            )

    def release(self, conn: Any) -> None:
        """Return a connection to the pool; discard broken connections."""
        try:
            # Roll back any uncommitted transaction so the connection is
            # returned in a clean state.
            if conn.info.transaction_status != 0:  # INTRANS or ERROR
                conn.rollback()
            self._pool.put_nowait(conn)
        except Exception:
            # Connection is broken — discard it and remove from tracking
            logger.warning("PostgresBackend: discarding broken connection", exc_info=True)
            with self._lock:
                try:
                    self._all_conns.remove(conn)
                except ValueError:
                    pass
            try:
                conn.close()
            except Exception:
                pass

    @contextlib.contextmanager
    def connection(self) -> Generator[Any, None, None]:
        """Context manager: acquire → yield → release."""
        conn = self.acquire()
        try:
            yield conn
        finally:
            self.release(conn)

    def close_all(self) -> None:
        """Close all connections in the pool. Call on application shutdown."""
        with self._lock:
            conns = list(self._all_conns)
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        logger.info("PostgresBackend: all pool connections closed")


class PostgresBackend:
    """PostgreSQL backend using psycopg v3 with a thread-safe connection pool.

    Cloud Run runs multiple concurrent requests within a single instance.
    A single connection would serialize all database operations; a pool
    allows requests to run concurrently while keeping the connection count
    bounded.

    Pool defaults (minconn=2, maxconn=10) are appropriate for Cloud Run
    instances with max-concurrency of 80 (the Cloud Run default).  The pool
    blocks for up to 30 s before raising RuntimeError, which surfaces as a
    503 rather than silently hanging.

    Thread-local connection pinning:
        ``database.py`` calls ``execute()`` then ``commit()`` as separate
        method calls on the same backend instance.  To keep these two calls
        on the *same* physical connection, each thread pins one connection
        from the pool (stored in ``threading.local()``) on the first
        ``execute()`` call and releases it back to the pool after
        ``commit()``.  This matches the behaviour of the SQLiteBackend, which
        also uses one connection per thread.

        For self-contained read operations (``fetchone`` / ``fetchall``),
        the connection is acquired, used, committed, and released within the
        call so as not to hold a pool slot longer than necessary.

    Parameter placeholders:
        SQLite uses ``?`` as the placeholder; PostgreSQL uses ``%s``.
        All SQL passed to this backend is translated automatically via
        ``_translate_params()`` before execution.
    """

    def __init__(self, postgres_url: str, minconn: int = 2,
                 maxconn: int = 10, pool_timeout: float = 30.0) -> None:
        self._pool = _PostgresConnectionPool(
            dsn=postgres_url,
            minconn=minconn,
            maxconn=maxconn,
            timeout=pool_timeout,
        )
        # Thread-local storage: each thread may hold at most one connection
        # from the pool at a time (pinned between execute() and commit()).
        self._local = threading.local()

    # ------------------------------------------------------------------
    # Thread-local connection helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> Any:
        """Return the thread-local pinned connection, acquiring one if needed."""
        if getattr(self._local, "conn", None) is None:
            self._local.conn = self._pool.acquire()
        return self._local.conn

    def _put_conn(self, *, rollback: bool = False) -> None:
        """Release the thread-local pinned connection back to the pool.

        If ``rollback=True``, roll back before releasing so the pool gets
        a clean connection.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        if rollback:
            try:
                conn.rollback()
            except Exception:
                pass
        self._pool.release(conn)

    # ------------------------------------------------------------------
    # DatabaseBackend protocol methods
    # ------------------------------------------------------------------

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute a single DML statement on the thread-local connection.

        The connection stays pinned to this thread until ``commit()`` is
        called (or an exception occurs, in which case it is released with
        rollback).  The returned cursor object has a valid ``rowcount``
        attribute.
        """
        translated = _translate_params(sql)
        conn = self._get_conn()
        try:
            return conn.execute(translated, params)
        except Exception:
            self._put_conn(rollback=True)
            raise

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        """Execute a DML statement for each parameter tuple, then commit."""
        translated = _translate_params(sql)
        conn = self._get_conn()
        try:
            conn.executemany(translated, params_list)
            conn.commit()
        except Exception:
            self._put_conn(rollback=True)
            raise
        self._put_conn()

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        """Execute a SELECT and return the first row as a dict (or None).

        Uses a fresh pool connection that is returned immediately after
        the query — does not pin the thread-local connection.
        """
        translated = _translate_params(sql)
        with self._pool.connection() as conn:
            cur = conn.execute(translated, params)
            row = cur.fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a SELECT and return all rows as dicts.

        Uses a fresh pool connection that is returned immediately after
        the query — does not pin the thread-local connection.
        """
        translated = _translate_params(sql)
        with self._pool.connection() as conn:
            cur = conn.execute(translated, params)
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def commit(self) -> None:
        """Commit the current transaction and release the thread-local connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # Nothing to commit (e.g., after a fetchone/fetchall-only path)
            return
        try:
            conn.commit()
        except Exception:
            self._put_conn(rollback=True)
            raise
        self._put_conn()

    def execute_ddl(self, ddl: str) -> None:
        """Execute DDL statements (CREATE TABLE, CREATE INDEX, etc.)."""
        with self._pool.connection() as conn:
            for statement in ddl.split(";"):
                stmt = statement.strip()
                if stmt:
                    conn.execute(stmt)
            conn.commit()

    def execute_pragma(self, pragma: str) -> None:
        # PRAGMAs are SQLite-only — no-op for Postgres
        pass

    @property
    def backend_type(self) -> str:
        return "postgres"

    # ------------------------------------------------------------------
    # Explicit pool access (for callers that want full lifecycle control)
    # ------------------------------------------------------------------

    def _pool_get_conn(self) -> Any:
        """Acquire a connection from the pool (caller must call _pool_put_conn)."""
        return self._pool.acquire()

    def _pool_put_conn(self, conn: Any) -> None:
        """Return a connection to the pool."""
        self._pool.release(conn)

    # ------------------------------------------------------------------
    # Extra Postgres-specific methods
    # ------------------------------------------------------------------

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
        with self._pool.connection() as conn:
            conn.execute(sql, values)
            conn.commit()

    def close(self) -> None:
        """Close all pool connections. Call on application shutdown."""
        self._pool.close_all()


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
