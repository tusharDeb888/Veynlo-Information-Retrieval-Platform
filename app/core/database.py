"""
Async SQLAlchemy engine, session factory, and the RLS-aware session dependency.

Every request-scoped session issues ``SET LOCAL`` statements so that
PostgreSQL Row-Level Security policies can read the current tenant / user
from ``current_setting('app.current_tenant_id', true)`` without any
application-level filtering.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

if TYPE_CHECKING:
    from app.core.security import CurrentUser

# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------

_settings = get_settings()

engine = create_async_engine(
    _settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=600,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# Declarative base shared by all models
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Base class for every SQLAlchemy ORM model in the platform."""

    pass


# ---------------------------------------------------------------------------
# RLS-aware session dependency
# ---------------------------------------------------------------------------


async def get_tenant_db_session(
    current_user: CurrentUser,
) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an *AsyncSession* with RLS context.

    Inside the transaction it executes::

        SET LOCAL app.current_tenant_id = '<tenant_id>';
        SET LOCAL app.current_user_id   = '<user_id>';

    so that every subsequent SQL statement in the same transaction is
    automatically scoped by the RLS policies defined in the migration.

    The ``SET LOCAL`` values are discarded when the transaction ends, so
    there is zero risk of leaking tenant context across pooled connections.
    """
    async with async_session_factory() as session, session.begin():
        # Both values MUST be valid UUID strings.
        tenant_id = str(current_user.tenant_id)
        user_id = str(current_user.user_id)

        # `set_config(..., true)` is parameter-safe and has the same
        # transaction-local lifetime as SET LOCAL.
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": tenant_id},
        )
        await session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": user_id},
        )
        yield session
        # commit happens automatically when the ``begin()`` block exits
        # without an exception; rollback on exception.


async def get_raw_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a raw *AsyncSession* **without** RLS context.

    Use this for admin / system operations that should not be tenant-scoped
    (e.g. creating tenants, the reaper task, etc.).
    """
    async with async_session_factory() as session, session.begin():
        yield session
