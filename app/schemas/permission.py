"""Pydantic v2 schemas for RBAC / ABAC permission grant & revoke flows."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Sub-models for grant / revoke payloads
# ---------------------------------------------------------------------------
class PermissionGrant(BaseModel):
    """Permissions to ADD to a document's ACL."""

    model_config = ConfigDict(strict=True)

    allowed_department_ids: list[UUID] = Field(
        default_factory=list,
        description="Department UUIDs to grant read access.",
    )
    allowed_roles: list[str] = Field(
        default_factory=list,
        description="Roles to grant read access (e.g. 'EMPLOYEE', 'DEPT_ADMIN').",
    )
    allowed_user_ids: list[UUID] = Field(
        default_factory=list,
        description="Explicit user UUIDs to grant read access.",
    )

    @model_validator(mode="after")
    def at_least_one_field(self) -> PermissionGrant:
        if not self.allowed_department_ids and not self.allowed_roles and not self.allowed_user_ids:
            raise ValueError(
                "At least one of allowed_department_ids, allowed_roles, "
                "or allowed_user_ids must be provided for a grant."
            )
        return self


class PermissionRevoke(BaseModel):
    """Permissions to REMOVE from a document's ACL."""

    model_config = ConfigDict(strict=True)

    allowed_department_ids: list[UUID] = Field(
        default_factory=list,
        description="Department UUIDs to revoke read access.",
    )
    allowed_roles: list[str] = Field(
        default_factory=list,
        description="Roles to revoke read access.",
    )
    allowed_user_ids: list[UUID] = Field(
        default_factory=list,
        description="Explicit user UUIDs to revoke read access.",
    )

    @model_validator(mode="after")
    def at_least_one_field(self) -> PermissionRevoke:
        if not self.allowed_department_ids and not self.allowed_roles and not self.allowed_user_ids:
            raise ValueError(
                "At least one of allowed_department_ids, allowed_roles, "
                "or allowed_user_ids must be provided for a revoke."
            )
        return self


# ---------------------------------------------------------------------------
# Combined request
# ---------------------------------------------------------------------------
class PermissionUpdateRequest(BaseModel):
    """Request to update (grant and/or revoke) permissions on a document."""

    model_config = ConfigDict(strict=True)

    grant: PermissionGrant | None = Field(
        default=None,
        description="Permissions to add.",
    )
    revoke: PermissionRevoke | None = Field(
        default=None,
        description="Permissions to remove.",
    )

    @model_validator(mode="after")
    def at_least_one_action(self) -> PermissionUpdateRequest:
        if self.grant is None and self.revoke is None:
            raise ValueError("At least one of 'grant' or 'revoke' must be provided.")
        return self


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
class PermissionEntry(BaseModel):
    """A single permission row for a document."""

    id: UUID
    document_id: UUID
    allowed_department_id: UUID | None = None
    allowed_role: str | None = None
    allowed_user_id: UUID | None = None


class PermissionResponse(BaseModel):
    """Response after a permission update with the full current permission set."""

    file_id: UUID
    permissions: list[PermissionEntry]
    message: str = "Permissions updated successfully."
