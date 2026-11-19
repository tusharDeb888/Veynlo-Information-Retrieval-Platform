"""
Centralised application configuration loaded from environment variables.

Uses Pydantic Settings v2 so every knob is typed, validated on startup, and
overridable via `.env` files or container env vars.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single source of truth for every tuneable in the platform."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── PostgreSQL ────────────────────────────────────────────────────────
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/rag",
        description="Async SQLAlchemy connection string (must use asyncpg driver).",
    )

    # ── Redis ─────────────────────────────────────────────────────────────
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL used as Celery broker and ACL cache backend.",
    )

    # ── MinIO / S3 ────────────────────────────────────────────────────────
    MINIO_ENDPOINT: str = Field(
        default="http://localhost:9000",
        validation_alias=AliasChoices("MINIO_ENDPOINT"),
        description="MinIO S3-compatible endpoint URL.",
    )
    MINIO_PUBLIC_ENDPOINT: str = Field(
        default="http://localhost:9000",
        description=(
            "Endpoint embedded in browser-facing presigned URLs. Set this to a "
            "publicly reachable MinIO address when the API runs in Docker."
        ),
    )
    MINIO_ACCESS_KEY: str = Field(
        default="minioadmin",
        description="MinIO root / access-key credential.",
    )
    MINIO_SECRET_KEY: str = Field(
        default="minioadmin",
        description="MinIO root / secret-key credential.",
    )
    MINIO_BUCKET: str = Field(
        default="knowledge-vault",
        description="Name of the S3 bucket for raw document storage.",
    )
    MINIO_REGION: str = Field(
        default="us-east-1",
        description="S3 region (MinIO default is us-east-1).",
    )
    MINIO_PRESIGN_EXPIRY: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Presigned URL validity in seconds.",
    )
    MULTIPART_PART_SIZE: int = Field(
        default=25 * 1024 * 1024,
        description="Part size in bytes for multipart uploads (25 MiB default).",
    )

    @field_validator("MINIO_ENDPOINT", "MINIO_PUBLIC_ENDPOINT", mode="before")
    @classmethod
    def _normalise_minio_endpoint(cls, value: str) -> str:
        """Accept both ``minio:9000`` and fully-qualified S3 endpoints."""
        value = str(value).strip().rstrip("/")
        if not value:
            raise ValueError("MinIO endpoint cannot be empty")
        if "://" not in value:
            return f"http://{value}"
        return value

    # ── Qdrant ────────────────────────────────────────────────────────────
    QDRANT_URL: str = Field(
        default="",
        description=("Qdrant database endpoint, for example https://<cluster>.<region>.<provider>.cloud.qdrant.io."),
    )
    QDRANT_API_KEY: str = Field(
        default="",
        description="Database API key for Qdrant Cloud. Never use a Cloud Management Key here.",
    )
    # Host/port remain as a local-development fallback when QDRANT_URL is unset.
    QDRANT_HOST: str = Field(default="localhost")
    QDRANT_PORT: int = Field(default=6333, description="Qdrant HTTP port.")
    QDRANT_GRPC_PORT: int = Field(default=6334, description="Qdrant gRPC port.")
    QDRANT_COLLECTION: str = Field(
        default="knowledge_chunks",
        description="Name of the shared multi-tenant Qdrant collection.",
    )

    @field_validator("QDRANT_URL", mode="before")
    @classmethod
    def _normalise_qdrant_url(cls, value: str) -> str:
        """Accept a Cloud endpoint with or without an explicit scheme."""
        value = str(value or "").strip().rstrip("/")
        if value and "://" not in value:
            return f"https://{value}"
        return value

    # ── Authentication ────────────────────────────────────────────────────
    AUTH_MODE: Literal["firebase", "local_jwt"] = Field(
        default="firebase",
        description=(
            "Set to 'local_jwt' to use a symmetric HMAC secret for tests instead of calling the Firebase Admin SDK."
        ),
    )
    FIREBASE_PROJECT_ID: str = Field(
        default="",
        description="Firebase project ID (required when AUTH_MODE=firebase).",
    )
    JWT_SECRET_KEY: str = Field(
        default="CHANGE-ME-FOR-TESTS",
        description="HMAC-SHA256 secret used when AUTH_MODE=local_jwt.",
    )
    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_AUDIENCE: str = Field(
        default="",
        description="Expected JWT audience claim (optional).",
    )

    # ── ACL Cache ─────────────────────────────────────────────────────────
    ACL_CACHE_TTL: int = Field(
        default=300,
        validation_alias=AliasChoices("ACL_CACHE_TTL", "ACL_CACHE_TTL_SECONDS"),
        ge=0,
        description="TTL in seconds for the Redis ACL permission cache.",
    )

    # ── ML Models ─────────────────────────────────────────────────────────
    EMBEDDING_MODEL: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        validation_alias=AliasChoices("EMBEDDING_MODEL", "DENSE_EMBEDDING_MODEL"),
        description="HuggingFace model identifier used for dense chunk embeddings.",
    )
    EMBEDDING_DIMENSION: int = Field(
        default=384,
        validation_alias=AliasChoices("EMBEDDING_DIMENSION", "DENSE_VECTOR_SIZE"),
        ge=1,
        description="Dimensionality of the dense embedding vectors.",
    )
    CROSS_ENCODER_MODEL: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description="HuggingFace cross-encoder model used for reranking.",
    )

    # ── Grounded answer synthesis ─────────────────────────────────────────
    LLM_PROVIDER: Literal["mock", "groq"] = Field(default="mock")
    GROQ_API_KEY: str = Field(default="")
    GROQ_MODEL: str = Field(default="llama-3.3-70b-versatile")
    LLM_MAX_TOKENS: int = Field(default=1024, ge=64, le=8192)
    LLM_TEMPERATURE: float = Field(default=0.1, ge=0.0, le=2.0)

    # ── Worker / Ingestion ────────────────────────────────────────────────
    CHUNK_SIZE_TOKENS: int = Field(default=512, ge=64)
    CHUNK_OVERLAP_TOKENS: int = Field(default=64, ge=0)
    INGEST_MICRO_BATCH: int = Field(
        default=64,
        ge=1,
        description="Number of chunks committed to Qdrant per micro-batch.",
    )
    REAPER_STALL_MINUTES: int = Field(
        default=15,
        ge=1,
        description="Mark an INDEXING document as stalled after this many minutes.",
    )
    MAX_UPLOAD_FILES: int = Field(
        default=20,
        ge=1,
        description="Maximum number of files in a single initiate batch.",
    )

    # ── Celery ────────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = Field(default="")
    CELERY_RESULT_BACKEND: str = Field(default="")

    @field_validator("CELERY_BROKER_URL", mode="before")
    @classmethod
    def _default_broker(cls, v: str, info) -> str:  # noqa: N805
        if v:
            return v
        redis_url = info.data.get("REDIS_URL", "redis://localhost:6379/0")
        return redis_url

    @field_validator("CELERY_RESULT_BACKEND", mode="before")
    @classmethod
    def _default_result_backend(cls, v: str, info) -> str:  # noqa: N805
        if v:
            return v
        redis_url = info.data.get("REDIS_URL", "redis://localhost:6379/0")
        return redis_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton *Settings* instance (cached after first call)."""
    return Settings()


# Backwards-compatible module singleton.  Service modules historically imported
# ``settings`` directly, while FastAPI dependencies use ``get_settings``.
# Keeping one cached instance makes both styles observe the same configuration.
settings = get_settings()
