"""
Document and DocumentPermission ORM models.

The ``documents`` and ``document_permissions`` tables are protected by
PostgreSQL Row-Level Security policies that scope every query to the
current tenant via ``current_setting('app.current_tenant_id', true)``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# ---------------------------------------------------------------------------
# Document status enumeration
# ---------------------------------------------------------------------------


class DocumentStatus(enum.StrEnum):
    """State-machine states for a document's lifecycle."""

    INITIATED = "INITIATED"
    UPLOADED = "UPLOADED"
    INDEXING = "INDEXING"
    READY = "READY"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
        index=True,
    )

    # File metadata
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # S3 / MinIO
    s3_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    s3_upload_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Ingestion state machine
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", create_type=True),
        nullable=False,
        default=DocumentStatus.INITIATED,
        server_default=text("'INITIATED'"),
    )
    last_completed_chunk: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=-1,
        server_default=text("-1"),
    )
    total_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("now()"),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    permissions: Mapped[list[DocumentPermission]] = relationship(
        "DocumentPermission",
        back_populates="document",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


# ---------------------------------------------------------------------------
# DocumentPermission model
# ---------------------------------------------------------------------------


class DocumentPermission(Base):
    """Granular access-control entries for a document.

    At least one of ``allowed_department_id``, ``allowed_role``, or
    ``allowed_user_id`` should be set.  Multiple rows per document form
    an OR-union of access grants.
    """

    __tablename__ = "document_permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Grant targets — at least one should be non-null
    allowed_department_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    allowed_role: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True,
    )
    allowed_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Relationships
    document: Mapped[Document] = relationship("Document", back_populates="permissions")
