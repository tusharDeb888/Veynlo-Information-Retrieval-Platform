"""Periodic recovery for ingestion jobs that stopped updating their checkpoint."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from celery import Task
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import async_session_factory
from app.models.document import Document, DocumentStatus
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _tenant_session(tenant_id) -> AsyncIterator[AsyncSession]:
    """Create a short transaction with the tenant RLS variable set."""
    async with async_session_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        yield session


async def _reap_stalled_documents() -> list[tuple[str, str]]:
    """Lease stalled rows and return them for dispatch after their commit.

    RLS is tenant-scoped, so the reaper enumerates tenants from the unprotected
    table and performs each document query in a tenant-scoped transaction.
    Updating ``updated_at`` acts as a lease: overlapping beat runs cannot emit
    duplicate recovery tasks for the same stale checkpoint.
    """
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.REAPER_STALL_MINUTES)
    async with async_session_factory() as session:
        tenant_ids = list((await session.execute(select(Tenant.id))).scalars())

    recovered: list[tuple[str, str]] = []
    for tenant_id in tenant_ids:
        async with _tenant_session(tenant_id) as session:
            result = await session.execute(
                select(Document.id)
                .where(
                    Document.status == DocumentStatus.INDEXING,
                    Document.updated_at < cutoff,
                )
                .with_for_update(skip_locked=True)
            )
            document_ids = list(result.scalars())
            if document_ids:
                await session.execute(
                    update(Document).where(Document.id.in_(document_ids)).values(updated_at=datetime.now(UTC))
                )
                recovered.extend((str(document_id), str(tenant_id)) for document_id in document_ids)
    return recovered


@celery_app.task(bind=True, name="app.workers.reaper_tasks.reap_stalled_documents")
def reap_stalled_documents(self: Task) -> dict[str, int]:
    """Re-dispatch stalled ingestion jobs without advancing their checkpoint."""
    from app.workers.ingest_tasks import ingest_document_task

    recovered = asyncio.run(_reap_stalled_documents())
    for document_id, tenant_id in recovered:
        ingest_document_task.delay(document_id, tenant_id)
    if recovered:
        logger.warning("Re-dispatched %d stalled ingestion document(s)", len(recovered))
    return {"requeued": len(recovered)}
