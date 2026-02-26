"""Alembic environment configuration.

Reads the database URL from application settings at runtime so that
credentials never appear in alembic.ini.

For SQLite: uses the WCAG_DB_PATH setting.
For PostgreSQL: uses the WCAG_POSTGRES_URL setting.
"""
from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context

logger = logging.getLogger("alembic.env")

# Alembic Config object — provides access to alembic.ini values
config = context.config

# Set up loggers from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_url() -> str:
    """Resolve the database URL from application settings."""
    try:
        from services.common.config import settings

        if settings.db_backend == "postgres" and settings.postgres_url:
            return settings.postgres_url
        # SQLite — convert file path to SQLAlchemy URL
        db_path = settings.db_path or "wcag_pipeline.db"
        return f"sqlite:///{db_path}"
    except Exception:
        # Fallback for running alembic CLI outside the app
        import os

        postgres_url = os.getenv("WCAG_POSTGRES_URL", "")
        if postgres_url:
            return postgres_url
        db_path = os.getenv("WCAG_DB_PATH", "wcag_pipeline.db")
        return f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emits SQL to stdout."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to the database."""
    from sqlalchemy import create_engine, pool

    url = _get_url()
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
