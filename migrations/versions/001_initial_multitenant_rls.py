"""001 – Initial multi-tenant schema with Row-Level Security.

Creates all core tables (tenants, departments, users, documents,
document_permissions) and enables PostgreSQL RLS on the ``documents`` and
``document_permissions`` tables with a ``tenant_isolation_policy`` that
reads ``current_setting('app.current_tenant_id', true)::uuid``.

Revision ID: 001_initial_multitenant_rls
Revises: (none)
Create Date: 2026-11-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Alembic revision identifiers
# ---------------------------------------------------------------------------
revision: str = "001_initial_multitenant_rls"
down_revision: str | None = None
branch_labels: tuple | None = None
depends_on: str | None = None


def upgrade() -> None:
    # -- ENUM types ---------------------------------------------------------
    user_role_enum = postgresql.ENUM(
        "TENANT_ADMIN",
        "DEPT_ADMIN",
        "EMPLOYEE",
        name="user_role",
        create_type=False,
    )
    user_role_enum.create(op.get_bind(), checkfirst=True)

    document_status_enum = postgresql.ENUM(
        "INITIATED",
        "UPLOADED",
        "INDEXING",
        "READY",
        "FAILED",
        name="document_status",
        create_type=False,
    )
    document_status_enum.create(op.get_bind(), checkfirst=True)

    # -- tenants ------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # -- departments --------------------------------------------------------
    op.create_table(
        "departments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
    )
    op.create_index("ix_departments_tenant_id", "departments", ["tenant_id"])

    # -- users --------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "firebase_uid",
            sa.String(128),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "department_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", user_role_enum, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
    )
    op.create_index("ix_users_firebase_uid", "users", ["firebase_uid"])
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_department_id", "users", ["department_id"])

    # -- documents ----------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "department_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "uploaded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(1024), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256_hash", sa.String(64), nullable=False),
        sa.Column("s3_key", sa.String(1024), unique=True, nullable=False),
        sa.Column("s3_upload_id", sa.String(512), nullable=True),
        sa.Column(
            "status",
            document_status_enum,
            nullable=False,
            server_default=sa.text("'INITIATED'"),
        ),
        sa.Column(
            "last_completed_chunk",
            sa.Integer,
            nullable=False,
            server_default=sa.text("-1"),
        ),
        sa.Column("total_chunks", sa.Integer, nullable=True),
        sa.Column(
            "retry_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])
    op.create_index("ix_documents_department_id", "documents", ["department_id"])
    op.create_index("ix_documents_uploaded_by", "documents", ["uploaded_by"])
    op.create_index(
        "ix_documents_status_updated_at",
        "documents",
        ["status", "updated_at"],
    )

    # -- document_permissions -----------------------------------------------
    op.create_table(
        "document_permissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "allowed_department_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("allowed_role", sa.String(50), nullable=True),
        sa.Column(
            "allowed_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_document_permissions_tenant_id",
        "document_permissions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_document_permissions_document_id",
        "document_permissions",
        ["document_id"],
    )
    op.create_index(
        "ix_document_permissions_allowed_department_id",
        "document_permissions",
        ["allowed_department_id"],
    )
    op.create_index(
        "ix_document_permissions_allowed_role",
        "document_permissions",
        ["allowed_role"],
    )
    op.create_index(
        "ix_document_permissions_allowed_user_id",
        "document_permissions",
        ["allowed_user_id"],
    )

    # ======================================================================
    # Row-Level Security
    # ======================================================================

    conn = op.get_bind()

    # -- Enable RLS on documents -------------------------------------------
    conn.execute(sa.text("ALTER TABLE documents ENABLE ROW LEVEL SECURITY"))
    conn.execute(sa.text("ALTER TABLE documents FORCE ROW LEVEL SECURITY"))
    conn.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation_policy ON documents
                USING (
                    tenant_id = current_setting('app.current_tenant_id', true)::uuid
                )
                WITH CHECK (
                    tenant_id = current_setting('app.current_tenant_id', true)::uuid
                )
            """
        )
    )

    # -- Enable RLS on document_permissions --------------------------------
    conn.execute(
        sa.text("ALTER TABLE document_permissions ENABLE ROW LEVEL SECURITY")
    )
    conn.execute(
        sa.text("ALTER TABLE document_permissions FORCE ROW LEVEL SECURITY")
    )
    conn.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation_policy ON document_permissions
                USING (
                    tenant_id = current_setting('app.current_tenant_id', true)::uuid
                )
                WITH CHECK (
                    tenant_id = current_setting('app.current_tenant_id', true)::uuid
                )
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Drop RLS policies first
    conn.execute(
        sa.text(
            "DROP POLICY IF EXISTS tenant_isolation_policy ON document_permissions"
        )
    )
    conn.execute(
        sa.text("ALTER TABLE document_permissions DISABLE ROW LEVEL SECURITY")
    )

    conn.execute(
        sa.text("DROP POLICY IF EXISTS tenant_isolation_policy ON documents")
    )
    conn.execute(
        sa.text("ALTER TABLE documents DISABLE ROW LEVEL SECURITY")
    )

    # Drop tables in dependency order
    op.drop_table("document_permissions")
    op.drop_table("documents")
    op.drop_table("users")
    op.drop_table("departments")
    op.drop_table("tenants")

    # Drop enum types
    op.execute(sa.text("DROP TYPE IF EXISTS document_status"))
    op.execute(sa.text("DROP TYPE IF EXISTS user_role"))
