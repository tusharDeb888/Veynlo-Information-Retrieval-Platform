"""FastAPI router – document permission management (ABAC grant / revoke).

Endpoint:
  PATCH  /api/v1/permissions/{file_id}  – add or revoke document permissions
"""

from __future__ import annotations

import asyncio
import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_tenant_db_session
from app.core.security import invalidate_acl_cache_for_document, require_dept_admin
from app.models.document import Document, DocumentPermission
from app.schemas.permission import (
    PermissionEntry,
    PermissionResponse,
    PermissionUpdateRequest,
)
from app.services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/permissions", tags=["permissions"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_qdrant: QdrantService | None = None


def _get_qdrant() -> QdrantService:
    global _qdrant  # noqa: PLW0603
    if _qdrant is None:
        _qdrant = QdrantService()
    return _qdrant


async def _collect_permission_entries(db: AsyncSession, file_id: _uuid.UUID) -> list[PermissionEntry]:
    """Load all permission rows for a document."""
    result = await db.execute(select(DocumentPermission).where(DocumentPermission.document_id == file_id))
    rows = result.scalars().all()
    return [
        PermissionEntry(
            id=row.id,
            document_id=row.document_id,
            allowed_department_id=row.allowed_department_id,
            allowed_role=row.allowed_role,
            allowed_user_id=row.allowed_user_id,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# PATCH /api/v1/permissions/{file_id}
# ---------------------------------------------------------------------------
@router.patch(
    "/{file_id}",
    response_model=PermissionResponse,
    summary="Update document permissions (grant / revoke)",
)
async def update_permissions(
    file_id: _uuid.UUID,
    body: PermissionUpdateRequest,
    current_user=Depends(require_dept_admin),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> PermissionResponse:
    """Grant or revoke read permissions on a document.

    Changes are propagated to:
    1. PostgreSQL ``document_permissions`` table
    2. Qdrant payload fields on every chunk belonging to this document
    3. Redis ACL cache (invalidated)
    """
    # --- Verify document exists and belongs to current tenant ---
    result = await db.execute(select(Document).where(Document.id == file_id))
    doc: Document | None = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    if doc.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant mismatch.")

    tenant_id = doc.tenant_id

    # -----------------------------------------------------------------
    # 1. REVOKE – delete matching permission rows
    # -----------------------------------------------------------------
    if body.revoke is not None:
        for dept_id in body.revoke.allowed_department_ids:
            await db.execute(
                delete(DocumentPermission).where(
                    DocumentPermission.document_id == file_id,
                    DocumentPermission.allowed_department_id == dept_id,
                )
            )
        for role in body.revoke.allowed_roles:
            await db.execute(
                delete(DocumentPermission).where(
                    DocumentPermission.document_id == file_id,
                    DocumentPermission.allowed_role == role,
                )
            )
        for user_id in body.revoke.allowed_user_ids:
            await db.execute(
                delete(DocumentPermission).where(
                    DocumentPermission.document_id == file_id,
                    DocumentPermission.allowed_user_id == user_id,
                )
            )

    # -----------------------------------------------------------------
    # 2. GRANT – insert new permission rows (avoid duplicates)
    # -----------------------------------------------------------------
    if body.grant is not None:
        for dept_id in body.grant.allowed_department_ids:
            existing = await db.execute(
                select(DocumentPermission).where(
                    DocumentPermission.document_id == file_id,
                    DocumentPermission.allowed_department_id == dept_id,
                )
            )
            if existing.scalar_one_or_none() is None:
                db.add(
                    DocumentPermission(
                        id=_uuid.uuid4(),
                        tenant_id=tenant_id,
                        document_id=file_id,
                        allowed_department_id=dept_id,
                    )
                )

        for role in body.grant.allowed_roles:
            existing = await db.execute(
                select(DocumentPermission).where(
                    DocumentPermission.document_id == file_id,
                    DocumentPermission.allowed_role == role,
                )
            )
            if existing.scalar_one_or_none() is None:
                db.add(
                    DocumentPermission(
                        id=_uuid.uuid4(),
                        tenant_id=tenant_id,
                        document_id=file_id,
                        allowed_role=role,
                    )
                )

        for user_id in body.grant.allowed_user_ids:
            existing = await db.execute(
                select(DocumentPermission).where(
                    DocumentPermission.document_id == file_id,
                    DocumentPermission.allowed_user_id == user_id,
                )
            )
            if existing.scalar_one_or_none() is None:
                db.add(
                    DocumentPermission(
                        id=_uuid.uuid4(),
                        tenant_id=tenant_id,
                        document_id=file_id,
                        allowed_user_id=user_id,
                    )
                )

    await db.flush()

    # -----------------------------------------------------------------
    # 3. Rebuild Qdrant payload for this document's chunks
    # -----------------------------------------------------------------
    # Re-read the full permission set after the commit
    all_perms = await _collect_permission_entries(db, file_id)

    allowed_departments = list({str(p.allowed_department_id) for p in all_perms if p.allowed_department_id})
    allowed_roles = list({p.allowed_role for p in all_perms if p.allowed_role})
    allowed_users = list({str(p.allowed_user_id) for p in all_perms if p.allowed_user_id})

    qdrant = _get_qdrant()
    try:
        await asyncio.to_thread(
            qdrant.set_payload,
            str(file_id),
            {
                "allowed_departments": allowed_departments,
                "allowed_roles": allowed_roles,
                "allowed_users": allowed_users,
            },
        )
    except Exception as exc:
        logger.exception("Qdrant payload sync failed for file_id=%s", file_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Permission update could not be synchronized to retrieval.",
        ) from exc

    await db.commit()

    # -----------------------------------------------------------------
    # 4. Invalidate Redis ACL cache
    # -----------------------------------------------------------------
    await invalidate_acl_cache_for_document(tenant_id, file_id, db)

    return PermissionResponse(
        file_id=file_id,
        permissions=all_perms,
    )
