"""Pydantic v2 schemas for presigned multipart upload initiate/complete flows."""

from __future__ import annotations

import math
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_BATCH_SIZE: int = 20
DEFAULT_PART_SIZE: int = 25 * 1024 * 1024  # 25 MiB


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class FileManifest(BaseModel):
    """Manifest for a single file in a batch upload request."""

    model_config = ConfigDict(strict=True)

    filename: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Original filename including extension.",
    )
    size_bytes: int = Field(
        ...,
        gt=0,
        le=500 * 1024 * 1024,  # 500 MiB hard cap per file
        description="File size in bytes.",
    )
    sha256_hash: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[a-fA-F0-9]{64}$",
        description="SHA-256 hex digest of the file contents.",
    )
    content_type: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="MIME type (e.g. application/pdf).",
    )
    allowed_department_ids: list[UUID] = Field(
        default_factory=list,
        description="Department UUIDs granted read access.",
    )
    allowed_roles: list[str] = Field(
        default_factory=list,
        description="Role strings granted read access (e.g. 'EMPLOYEE').",
    )
    allowed_user_ids: list[UUID] = Field(
        default_factory=list,
        description="Explicit user UUIDs granted read access.",
    )

    @property
    def num_parts(self) -> int:
        """Calculate the number of multipart upload parts needed."""
        return max(1, math.ceil(self.size_bytes / DEFAULT_PART_SIZE))


class BatchUploadRequest(BaseModel):
    """Request to initiate a batch of multipart uploads (max 20 files)."""

    model_config = ConfigDict(strict=True)

    files: list[FileManifest] = Field(
        ...,
        min_length=1,
        max_length=MAX_BATCH_SIZE,
        description="File manifests (120 files per batch).",
    )

    @field_validator("files")
    @classmethod
    def validate_batch_size(cls, v: list[FileManifest]) -> list[FileManifest]:
        if len(v) > MAX_BATCH_SIZE:
            raise ValueError(f"Batch size {len(v)} exceeds maximum of {MAX_BATCH_SIZE}.")
        return v


class CompletePartInfo(BaseModel):
    """Part metadata returned by the client after uploading to presigned URL."""

    model_config = ConfigDict(strict=True)

    PartNumber: int = Field(..., ge=1, description="1-based part number.")
    ETag: str = Field(
        ...,
        min_length=1,
        description="ETag returned by MinIO/S3 after uploading the part.",
    )


class CompleteUploadRequest(BaseModel):
    """Request to finalise a multipart upload with part ETags."""

    model_config = ConfigDict(strict=True)

    parts: list[CompletePartInfo] = Field(
        ...,
        min_length=1,
        description="List of uploaded parts with PartNumber and ETag.",
    )

    @field_validator("parts")
    @classmethod
    def validate_part_order(cls, v: list[CompletePartInfo]) -> list[CompletePartInfo]:
        """Ensure parts are in ascending order by PartNumber with no gaps."""
        sorted_parts = sorted(v, key=lambda p: p.PartNumber)
        expected = list(range(1, len(sorted_parts) + 1))
        actual = [p.PartNumber for p in sorted_parts]
        if actual != expected:
            raise ValueError("Parts must be consecutively numbered starting from 1 with no gaps.")
        return sorted_parts


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
class PresignedPartUrl(BaseModel):
    """A single presigned URL for one upload part."""

    part_number: int = Field(..., ge=1, description="1-based part number.")
    url: str = Field(..., description="Presigned PUT URL for this part.")


class InitiateUploadResponse(BaseModel):
    """Response for a single file's initiated multipart upload."""

    file_id: UUID
    upload_id: str
    part_size: int = Field(
        default=DEFAULT_PART_SIZE,
        description="Recommended part size in bytes.",
    )
    presigned_urls: list[PresignedPartUrl]


class BatchInitiateResponse(BaseModel):
    """Aggregated response for a batch upload initiation."""

    uploads: list[InitiateUploadResponse]


class UploadStatusResponse(BaseModel):
    """Current status of a document upload / ingestion pipeline."""

    file_id: UUID
    filename: str
    status: str = Field(
        ...,
        description="One of: INITIATED, UPLOADED, INDEXING, READY, FAILED.",
    )
    size_bytes: int
    content_type: str
    last_completed_chunk: int = Field(
        default=-1,
        description="Index of the last successfully ingested chunk (-1 if none).",
    )
    total_chunks: int | None = Field(
        default=None,
        description="Total chunk count (populated once ingestion completes).",
    )
    retry_count: int = 0
    error_message: str | None = None
    updated_at: datetime | None = None
