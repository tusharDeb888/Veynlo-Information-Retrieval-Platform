"""
Tenant, Department, and User ORM models.

SQLAlchemy 2.0 declarative style with ``mapped_column`` and ``Mapped`` type
annotations for first-class async support.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# ---------------------------------------------------------------------------
# Role enumeration
# ---------------------------------------------------------------------------


class UserRole(enum.StrEnum):
    """Platform-wide role labels."""

    TENANT_ADMIN = "TENANT_ADMIN"
    DEPT_ADMIN = "DEPT_ADMIN"
    EMPLOYEE = "EMPLOYEE"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("now()"),
    )

    # Relationships
    departments: Mapped[list[Department]] = relationship(
        "Department",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    users: Mapped[list[User]] = relationship(
        "User",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Department(Base):
    __tablename__ = "departments"

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
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Relationships
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="departments")
    users: Mapped[list[User]] = relationship(
        "User",
        back_populates="department",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    firebase_uid: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
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
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", create_type=True),
        nullable=False,
        default=UserRole.EMPLOYEE,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)

    # Relationships
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")
    department: Mapped[Department] = relationship("Department", back_populates="users")
