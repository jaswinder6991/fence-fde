"""SQLAlchemy engine and session helpers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from fence_pipeline.models import Base

DEFAULT_DSN = "postgresql://fence:fence@localhost:5432/fence"

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_dsn() -> str:
    return os.environ.get("FENCE_DATABASE_URL", DEFAULT_DSN)


def sqlalchemy_url(dsn: str | None = None) -> str:
    """Convert a libpq-style URL to a SQLAlchemy psycopg3 URL."""
    url = dsn or get_dsn()
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


def get_engine(dsn: str | None = None) -> Engine:
    global _engine, _SessionLocal
    url = sqlalchemy_url(dsn)
    if _engine is None or str(_engine.url) != url:
        _engine = create_engine(url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def ensure_schema(dsn: str | None = None) -> None:
    """Create tables from ORM metadata if they do not exist.

    Recreates receivables_current when an older per-run_id shape is present.
    Ensures indexes added after first boot exist on long-lived databases.
    """
    engine = get_engine(dsn)
    Base.metadata.create_all(engine)
    _migrate_receivables_current_if_needed(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_payment_allocations_recv
                ON payment_allocations (receivable_external_id, installment_seq)
                """
            )
        )


def _migrate_receivables_current_if_needed(engine: Engine) -> None:
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'receivables_current'
                """
            )
        ).scalar()
        if not exists:
            return
        has_active = conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'receivables_current'
                  AND column_name = 'active'
                """
            )
        ).scalar()
        if has_active:
            return
        conn.execute(text("DROP TABLE receivables_current CASCADE"))
        conn.commit()
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["receivables_current"]])


@contextmanager
def session_scope(dsn: str | None = None) -> Iterator[Session]:
    get_engine(dsn)
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def truncate_all(session: Session) -> None:
    """Truncate all pipeline tables (tests)."""
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(text(f"TRUNCATE TABLE {table.name} CASCADE"))
    session.commit()
