"""FastAPI router – multipart upload initiation, completion & status.

Endpoints:
  POST   /api/v1/uploads/initiate        – batch initiate multipart uploads
  POST   /api/v1/uploads/{file_id}/complete – finalise a multipart upload
  GET    /api/v1/uploads/{file_id}/status   – poll ingestion status
"""

from __future__ import annotations

import logging
import math
import uuid as _uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_tenant_db_session
from app.core.security import require_dept_admin
from app.models.document import Document, DocumentPermission, DocumentStatus
from app.schemas.upload import (
    DEFAULT_PART_SIZE,
    BatchInitiateResponse,
    BatchUploadRequest,
    CompleteUploadRequest,
    InitiateUploadResponse,
    PresignedPartUrl,
    UploadStatusResponse,
)
from app.services.minio_service import MinIOService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_minio: MinIOService | None = None


def _get_minio() -> MinIOService:
    """Lazy singleton accessor for MinIOService."""
    global _minio  # noqa: PLW0603
    if _minio is None:
        _minio = MinIOService()
    return _minio


def _s3_key(tenant_id: _uuid.UUID, file_id: _uuid.UUID, filename: str) -> str:
    """Deterministic S3 object key for a document."""
    return f"tenants/{tenant_id}/documents/{file_id}/{filename}"


# ---------------------------------------------------------------------------
# POST /api/v1/uploads/initiate
# ---------------------------------------------------------------------------
@router.post(
    "/initiate",
    response_model=BatchInitiateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initiate batch multipart uploads",
)
async def initiate_batch_upload(
    body: BatchUploadRequest,
    current_user=Depends(require_dept_admin),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> BatchInitiateResponse:
    """Accept up to 20 file manifests, create DB rows and presigned URLs.

    The caller's browser/client uploads parts directly to MinIO using the
    presigned URLs – no file bytes ever touch FastAPI.
    """
    minio = _get_minio()
    from app.core.config import settings

    bucket = settings.MINIO_BUCKET
    tenant_id: _uuid.UUID = current_user.tenant_id
    department_id: _uuid.UUID = current_user.department_id

    uploads: list[InitiateUploadResponse] = []

    for manifest in body.files:
        file_id = _uuid.uuid4()
        s3_key = _s3_key(tenant_id, file_id, manifest.filename)
        num_parts = max(1, math.ceil(manifest.size_bytes / DEFAULT_PART_SIZE))

        # --- MinIO: create multipart upload ---
        upload_id = await minio.create_multipart_upload(bucket, s3_key)

        # --- Presigned part URLs ---
        raw_urls = await minio.generate_presigned_part_urls(bucket, s3_key, upload_id, num_parts, DEFAULT_PART_SIZE)
        presigned_urls = [PresignedPartUrl(part_number=entry["part_number"], url=entry["url"]) for entry in raw_urls]

        # --- PostgreSQL: insert document row ---
        doc = Document(
            id=file_id,
            tenant_id=tenant_id,
            department_id=department_id,
            uploaded_by=current_user.user_id,
            filename=manifest.filename,
            content_type=manifest.content_type,
            size_bytes=manifest.size_bytes,
            sha256_hash=manifest.sha256_hash,
            s3_key=s3_key,
            s3_upload_id=upload_id,
            status=DocumentStatus.INITIATED,
            last_completed_chunk=-1,
            retry_count=0,
            updated_at=datetime.now(UTC),
        )
        db.add(doc)

        # --- PostgreSQL: insert permission rows ---
        for dept_id in manifest.allowed_department_ids:
            db.add(
                DocumentPermission(
                    id=_uuid.uuid4(),
                    tenant_id=tenant_id,
                    document_id=file_id,
                    allowed_department_id=dept_id,
                )
            )
        for role in manifest.allowed_roles:
            db.add(
                DocumentPermission(
                    id=_uuid.uuid4(),
                    tenant_id=tenant_id,
                    document_id=file_id,
                    allowed_role=role,
                )
            )
        for user_id in manifest.allowed_user_ids:
            db.add(
                DocumentPermission(
                    id=_uuid.uuid4(),
                    tenant_id=tenant_id,
                    document_id=file_id,
                    allowed_user_id=user_id,
                )
            )

        uploads.append(
            InitiateUploadResponse(
                file_id=file_id,
                upload_id=upload_id,
                part_size=DEFAULT_PART_SIZE,
                presigned_urls=presigned_urls,
            )
        )

    # Commit all document + permission rows in one transaction.
    await db.commit()
    logger.info(
        "Batch upload initiated: %d files for tenant %s",
        len(uploads),
        tenant_id,
    )

    return BatchInitiateResponse(uploads=uploads)


# ---------------------------------------------------------------------------
# POST /api/v1/uploads/{file_id}/complete
# ---------------------------------------------------------------------------
@router.post(
    "/{file_id}/complete",
    response_model=UploadStatusResponse,
    summary="Complete a multipart upload",
)
async def complete_upload(
    file_id: _uuid.UUID,
    body: CompleteUploadRequest,
    current_user=Depends(require_dept_admin),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> UploadStatusResponse:
    """Finalise a multipart upload: assemble parts, verify, dispatch ingestion."""
    minio = _get_minio()
    from app.core.config import settings

    bucket = settings.MINIO_BUCKET

    # Fetch document
    result = await db.execute(select(Document).where(Document.id == file_id))
    doc: Document | None = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if doc.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch.")
    if doc.status != DocumentStatus.INITIATED:
        raise HTTPException(
            status_code=409,
            detail=f"Document is in '{doc.status.value}' state; expected INITIATED.",
        )
    if doc.s3_upload_id is None:
        raise HTTPException(status_code=409, detail="No multipart upload ID on record.")

    # --- MinIO: complete multipart upload ---
    parts_for_minio = [{"PartNumber": p.PartNumber, "ETag": p.ETag} for p in body.parts]
    await minio.complete_multipart_upload(bucket, doc.s3_key, doc.s3_upload_id, parts_for_minio)

    # --- MinIO: verify object ---
    head = await minio.head_object(bucket, doc.s3_key)
    remote_size = head.get("ContentLength", head.get("content_length", 0))
    if remote_size != doc.size_bytes:
        logger.error(
            "Size mismatch for %s: expected=%d got=%d",
            file_id,
            doc.size_bytes,
            remote_size,
        )
        doc.status = DocumentStatus.FAILED
        doc.s3_upload_id = None
        doc.error_message = f"Object size mismatch: expected {doc.size_bytes} bytes, got {remote_size} bytes."
        doc.updated_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded object size does not match the declared manifest.",
        )

    # --- PostgreSQL: transition to UPLOADED ---
    doc.status = DocumentStatus.UPLOADED
    doc.s3_upload_id = None  # Upload ID no longer needed
    doc.updated_at = datetime.now(UTC)
    await db.commit()

    # --- Dispatch Celery ingestion task ---
    from app.workers.ingest_tasks import ingest_document_task

    ingest_document_task.delay(str(file_id), str(doc.tenant_id))
    logger.info("Dispatched ingestion task for file_id=%s", file_id)

    return UploadStatusResponse(
        file_id=doc.id,
        filename=doc.filename,
        status=doc.status.value,
        size_bytes=doc.size_bytes,
        content_type=doc.content_type,
        last_completed_chunk=doc.last_completed_chunk,
        total_chunks=doc.total_chunks,
        retry_count=doc.retry_count,
        error_message=doc.error_message,
        updated_at=doc.updated_at,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/uploads/{file_id}/status
# ---------------------------------------------------------------------------
@router.get(
    "/{file_id}/status",
    response_model=UploadStatusResponse,
    summary="Get upload / ingestion status",
)
async def get_upload_status(
    file_id: _uuid.UUID,
    current_user=Depends(require_dept_admin),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> UploadStatusResponse:
    """Return the current status of a document's upload and ingestion."""
    result = await db.execute(select(Document).where(Document.id == file_id))
    doc: Document | None = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if doc.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch.")

    return UploadStatusResponse(
        file_id=doc.id,
        filename=doc.filename,
        status=doc.status.value,
        size_bytes=doc.size_bytes,
        content_type=doc.content_type,
        last_completed_chunk=doc.last_completed_chunk,
        total_chunks=doc.total_chunks,
        retry_count=doc.retry_count,
        error_message=doc.error_message,
        updated_at=doc.updated_at,
    )
