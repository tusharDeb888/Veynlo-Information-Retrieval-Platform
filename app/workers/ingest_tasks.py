"""Crash-safe Celery task for turning uploaded documents into Qdrant points.

The task deliberately commits its progress only *after* Qdrant has accepted a
micro-batch.  Point ids are UUIDv5 values derived from the tenant, document,
and chunk index, so a worker that dies between the Qdrant write and the
PostgreSQL checkpoint simply upserts the same points when it is redelivered.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from celery import Task
from qdrant_client.models import PointStruct
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import async_session_factory
from app.models.document import Document, DocumentPermission, DocumentStatus
from app.services.embedding_service import EmbeddingService, build_sparse_vector
from app.services.qdrant_service import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME, get_qdrant_service
from app.workers.chunker import Chunk, _get_s3_client, fast_forward_chunks, stream_chunks

logger = logging.getLogger(__name__)

_POINT_NAMESPACE = uuid.UUID("ac43b73e-3527-5e88-bd1f-a086f6c1da0a")
_MAX_ERROR_LENGTH = 2_000


@dataclass(frozen=True, slots=True)
class _DocumentSnapshot:
    """The immutable ingestion inputs captured before slow external work."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    department_id: uuid.UUID
    filename: str
    content_type: str
    s3_key: str
    last_completed_chunk: int
    allowed_departments: list[str]
    allowed_roles: list[str]
    allowed_users: list[str]


@asynccontextmanager
async def _tenant_session(tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """Open a transaction with the RLS tenant variable set for a worker."""
    async with async_session_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        yield session


def _point_id(tenant_id: uuid.UUID, document_id: uuid.UUID, chunk_index: int) -> str:
    """Return a stable Qdrant point id for one document chunk."""
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{tenant_id}:{document_id}:{chunk_index}"))


def _acl_lists(permissions: Sequence[DocumentPermission]) -> tuple[list[str], list[str], list[str]]:
    """Convert relational permission rows to the Qdrant ACL payload shape."""
    departments = sorted({str(p.allowed_department_id) for p in permissions if p.allowed_department_id is not None})
    roles = sorted({p.allowed_role for p in permissions if p.allowed_role})
    users = sorted({str(p.allowed_user_id) for p in permissions if p.allowed_user_id is not None})
    return departments, roles, users


async def _claim_document(document_id: uuid.UUID, tenant_id: uuid.UUID) -> _DocumentSnapshot | None:
    """Claim a document for indexing and snapshot the data needed by the task.

    The transaction is intentionally short.  It transitions UPLOADED to
    INDEXING, but permits an already INDEXING redelivery to resume from its
    database checkpoint.
    """
    async with _tenant_session(tenant_id) as session:
        result = await session.execute(
            select(Document)
            .options(selectinload(Document.permissions))
            .where(Document.id == document_id, Document.tenant_id == tenant_id)
            .with_for_update()
        )
        document = result.scalar_one_or_none()
        if document is None:
            logger.warning("Ingestion requested for unknown document=%s tenant=%s", document_id, tenant_id)
            return None
        if document.status is DocumentStatus.READY:
            return None
        if document.status not in (DocumentStatus.UPLOADED, DocumentStatus.INDEXING):
            logger.info("Skipping document=%s in state=%s", document_id, document.status.value)
            return None

        document.status = DocumentStatus.INDEXING
        document.error_message = None
        document.updated_at = datetime.now(UTC)
        departments, roles, users = _acl_lists(document.permissions)
        return _DocumentSnapshot(
            id=document.id,
            tenant_id=document.tenant_id,
            department_id=document.department_id,
            filename=document.filename,
            content_type=document.content_type,
            s3_key=document.s3_key,
            last_completed_chunk=document.last_completed_chunk,
            allowed_departments=departments,
            allowed_roles=roles,
            allowed_users=users,
        )


async def _checkpoint(document_id: uuid.UUID, tenant_id: uuid.UUID, chunk_index: int) -> None:
    """Persist a monotonic checkpoint after its Qdrant batch is durable."""
    async with _tenant_session(tenant_id) as session:
        await session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
                Document.status == DocumentStatus.INDEXING,
                Document.last_completed_chunk < chunk_index,
            )
            .values(last_completed_chunk=chunk_index, updated_at=datetime.now(UTC), error_message=None)
        )


async def _mark_ready(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    total_chunks: int,
    last_completed_chunk: int,
) -> None:
    """Mark the document ready only once every chunk has been upserted."""
    async with _tenant_session(tenant_id) as session:
        await session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
                Document.status == DocumentStatus.INDEXING,
            )
            .values(
                status=DocumentStatus.READY,
                total_chunks=total_chunks,
                last_completed_chunk=last_completed_chunk,
                error_message=None,
                updated_at=datetime.now(UTC),
            )
        )


async def _record_failure(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    exc: BaseException,
    *,
    terminal: bool,
) -> None:
    """Store retry diagnostics without losing the resumable checkpoint."""
    error_message = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_LENGTH]
    async with _tenant_session(tenant_id) as session:
        values: dict[str, object] = {
            "retry_count": Document.retry_count + 1,
            "error_message": error_message,
            "updated_at": datetime.now(UTC),
        }
        if terminal:
            values["status"] = DocumentStatus.FAILED
        await session.execute(
            update(Document).where(Document.id == document_id, Document.tenant_id == tenant_id).values(**values)
        )


def _s3_connection_settings() -> tuple[str, bool]:
    """Normalise either ``minio:9000`` or an HTTP(S) endpoint for boto3."""
    endpoint = get_settings().MINIO_ENDPOINT.strip()
    parsed = urlparse(endpoint if "://" in endpoint else f"http://{endpoint}")
    if not parsed.netloc:
        raise ValueError("MINIO_ENDPOINT must contain a host, optionally with a scheme")
    return parsed.netloc, parsed.scheme == "https"


def _batched(chunks: Sequence[Chunk], size: int) -> list[Sequence[Chunk]]:
    return [chunks[offset : offset + size] for offset in range(0, len(chunks), size)]


async def _ingest_document(document_id: uuid.UUID, tenant_id: uuid.UUID) -> dict[str, int | str]:
    snapshot = await _claim_document(document_id, tenant_id)
    if snapshot is None:
        return {"document_id": str(document_id), "status": "skipped"}

    settings = get_settings()
    endpoint, use_ssl = _s3_connection_settings()
    s3_client = _get_s3_client(
        endpoint,
        settings.MINIO_ACCESS_KEY,
        settings.MINIO_SECRET_KEY,
        use_ssl=use_ssl,
    )
    chunk_generator = fast_forward_chunks(
        stream_chunks(
            s3_client,
            settings.MINIO_BUCKET,
            snapshot.s3_key,
            snapshot.content_type,
            snapshot.filename,
            target_tokens=settings.CHUNK_SIZE_TOKENS,
            overlap_tokens=settings.CHUNK_OVERLAP_TOKENS,
        ),
        snapshot.last_completed_chunk,
    )
    qdrant = get_qdrant_service()
    embeddings = EmbeddingService()
    last_seen = snapshot.last_completed_chunk
    total_chunks = snapshot.last_completed_chunk + 1
    pending: list[Chunk] = []

    def flush(batch: Sequence[Chunk]) -> None:
        nonlocal last_seen
        vectors = embeddings.embed_texts([chunk.text for chunk in batch])
        if len(vectors) != len(batch):
            raise RuntimeError("Embedding service returned an unexpected vector count")
        points = [
            PointStruct(
                id=_point_id(snapshot.tenant_id, snapshot.id, chunk.index),
                vector={
                    DENSE_VECTOR_NAME: vector,
                    SPARSE_VECTOR_NAME: build_sparse_vector(chunk.text),
                },
                payload={
                    "tenant_id": str(snapshot.tenant_id),
                    "department_id": str(snapshot.department_id),
                    "file_id": str(snapshot.id),
                    "filename": snapshot.filename,
                    "chunk_index": chunk.index,
                    "page_number": chunk.page_number,
                    "text": chunk.text,
                    "allowed_departments": snapshot.allowed_departments,
                    "allowed_roles": snapshot.allowed_roles,
                    "allowed_users": snapshot.allowed_users,
                },
            )
            for chunk, vector in zip(batch, vectors, strict=True)
        ]
        # wait=True in the service makes this the durable side of the checkpoint.
        qdrant.upsert_points(points)
        last_seen = batch[-1].index

    try:
        for chunk in chunk_generator:
            total_chunks = max(total_chunks, chunk.index + 1)
            pending.append(chunk)
            if len(pending) >= settings.INGEST_MICRO_BATCH:
                flush(pending)
                await _checkpoint(snapshot.id, snapshot.tenant_id, last_seen)
                pending.clear()
        if pending:
            flush(pending)
            await _checkpoint(snapshot.id, snapshot.tenant_id, last_seen)
    finally:
        s3_client.close()

    await _mark_ready(
        snapshot.id,
        snapshot.tenant_id,
        total_chunks=total_chunks,
        last_completed_chunk=last_seen,
    )
    logger.info("Document ingestion ready document=%s chunks=%d", snapshot.id, total_chunks)
    return {"document_id": str(snapshot.id), "status": "ready", "total_chunks": total_chunks}


@celery_app.task(
    bind=True,
    name="app.workers.ingest_tasks.ingest_document_task",
    max_retries=3,
    default_retry_delay=30,
)
def ingest_document_task(self: Task, file_id: str, tenant_id: str) -> dict[str, int | str]:
    """Index one document, retrying transient failures with resumable state."""
    document_uuid = uuid.UUID(file_id)
    tenant_uuid = uuid.UUID(tenant_id)
    try:
        return asyncio.run(_ingest_document(document_uuid, tenant_uuid))
    except Exception as exc:
        terminal = self.request.retries >= self.max_retries
        logger.exception(
            "Document ingestion failed document=%s retry=%s terminal=%s",
            document_uuid,
            self.request.retries,
            terminal,
        )
        try:
            asyncio.run(_record_failure(document_uuid, tenant_uuid, exc, terminal=terminal))
        except Exception:
            logger.exception("Could not record ingestion failure document=%s", document_uuid)
        if terminal:
            raise
        raise self.retry(exc=exc, countdown=min(30 * (2**self.request.retries), 300)) from exc
