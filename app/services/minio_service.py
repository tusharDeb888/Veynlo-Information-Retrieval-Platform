"""
MinIO / S3-compatible object storage service.

Provides async helpers for multipart upload lifecycle and object streaming
using aioboto3.  All heavy I/O is fully async – no file bytes ever transit
through FastAPI memory.

References
----------
- MinIO S3 API compat: https://docs.min.io/aistor/developers/s3-api-compatibility/
- aioboto3 docs:       https://aioboto3.readthedocs.io/en/latest/
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
from botocore.config import Config as BotoConfig

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_PART_SIZE: int = 25 * 1024 * 1024  # 25 MiB per part
PRESIGNED_URL_EXPIRY: int = 3600  # 1 hour


class MinIOService:
    """Singleton async wrapper around an aioboto3 S3 resource/client."""

    _instance: MinIOService | None = None

    def __new__(cls) -> MinIOService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialised = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialised:  # type: ignore[has-type]
            return
        self._session = aioboto3.Session()
        self._endpoint_url = settings.MINIO_ENDPOINT
        self._access_key = settings.MINIO_ACCESS_KEY
        self._secret_key = settings.MINIO_SECRET_KEY
        self._region = getattr(settings, "MINIO_REGION", "us-east-1")
        self._boto_config = BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "adaptive"},
        )
        self._initialised = True

    # ------------------------------------------------------------------
    # Context-managed client
    # ------------------------------------------------------------------
    @asynccontextmanager
    async def _client(self, endpoint_url: str | None = None) -> AsyncIterator[Any]:
        """Yield a short-lived async S3 client from the session."""
        async with self._session.client(
            "s3",
            endpoint_url=endpoint_url or self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
            config=self._boto_config,
        ) as client:
            yield client

    # ------------------------------------------------------------------
    # Multipart upload lifecycle
    # ------------------------------------------------------------------
    async def create_multipart_upload(
        self,
        bucket: str,
        key: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Initiate an S3 multipart upload and return the ``UploadId``.

        Parameters
        ----------
        bucket:
            Target S3 bucket name.
        key:
            Object key (path) inside the bucket.
        content_type:
            MIME type for the resulting object.

        Returns
        -------
        str
            The ``UploadId`` issued by S3/MinIO.
        """
        async with self._client() as s3:
            resp = await s3.create_multipart_upload(
                Bucket=bucket,
                Key=key,
                ContentType=content_type,
            )
            upload_id: str = resp["UploadId"]
            logger.info(
                "Created multipart upload – bucket=%s key=%s upload_id=%s",
                bucket,
                key,
                upload_id,
            )
            return upload_id

    async def generate_presigned_part_urls(
        self,
        bucket: str,
        key: str,
        upload_id: str,
        num_parts: int,
        part_size: int = DEFAULT_PART_SIZE,
    ) -> list[dict[str, Any]]:
        """Generate presigned PUT URLs for each part of a multipart upload.

        Parameters
        ----------
        bucket, key, upload_id:
            Coordinates of the previously initiated multipart upload.
        num_parts:
            Total number of parts.
        part_size:
            Expected byte size of each part (last part may be smaller).

        Returns
        -------
        list[dict]
            ``[{"part_number": 1, "url": "https://...", "part_size": ...}, ...]``
        """
        presigned_urls: list[dict[str, Any]] = []
        # Presigned URLs are consumed by clients outside Docker.  Signing with
        # the public endpoint prevents URLs containing the internal `minio`
        # service hostname, which browsers cannot resolve.
        async with self._client(settings.MINIO_PUBLIC_ENDPOINT) as s3:
            for part_number in range(1, num_parts + 1):
                url = await s3.generate_presigned_url(
                    ClientMethod="upload_part",
                    Params={
                        "Bucket": bucket,
                        "Key": key,
                        "UploadId": upload_id,
                        "PartNumber": part_number,
                    },
                    ExpiresIn=PRESIGNED_URL_EXPIRY,
                )
                presigned_urls.append(
                    {
                        "part_number": part_number,
                        "url": url,
                        "part_size": part_size,
                    }
                )
        logger.info(
            "Generated %d presigned part URLs – bucket=%s key=%s upload_id=%s",
            num_parts,
            bucket,
            key,
            upload_id,
        )
        return presigned_urls

    async def complete_multipart_upload(
        self,
        bucket: str,
        key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Finalise a multipart upload by submitting the manifest of ETags.

        Parameters
        ----------
        parts:
            ``[{"PartNumber": 1, "ETag": "\"abc...\""}, ...]``

        Returns
        -------
        dict
            The S3 ``CompleteMultipartUpload`` response.
        """
        # S3 requires parts sorted by PartNumber
        sorted_parts = sorted(parts, key=lambda p: p["PartNumber"])
        async with self._client() as s3:
            resp = await s3.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": sorted_parts},
            )
            logger.info(
                "Completed multipart upload – bucket=%s key=%s upload_id=%s",
                bucket,
                key,
                upload_id,
            )
            return resp

    async def abort_multipart_upload(
        self,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> None:
        """Abort an in-progress multipart upload and release all uploaded parts."""
        async with self._client() as s3:
            await s3.abort_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
            )
            logger.warning(
                "Aborted multipart upload – bucket=%s key=%s upload_id=%s",
                bucket,
                key,
                upload_id,
            )

    # ------------------------------------------------------------------
    # Object read operations
    # ------------------------------------------------------------------
    async def head_object(self, bucket: str, key: str) -> dict[str, Any]:
        """Return object metadata without downloading the body.

        Returns
        -------
        dict
            Keys include ``ContentLength``, ``ContentType``, ``ETag``,
            ``LastModified``, etc.
        """
        async with self._client() as s3:
            resp = await s3.head_object(Bucket=bucket, Key=key)
            return {
                "ContentLength": resp["ContentLength"],
                "ContentType": resp.get("ContentType", "application/octet-stream"),
                "ETag": resp.get("ETag", ""),
                "LastModified": resp.get("LastModified"),
            }

    async def get_object_stream(
        self,
        bucket: str,
        key: str,
        range_header: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield the object body as an async byte-chunk stream.

        This is designed for constant-memory consumption: the caller
        iterates over chunks without ever buffering the full object.

        Parameters
        ----------
        range_header:
            Optional HTTP-style byte range, e.g. ``"bytes=0-1048575"``.

        Yields
        ------
        bytes
            Chunks of the object body (typically 64 KiB each).
        """
        params: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if range_header:
            params["Range"] = range_header

        async with self._client() as s3:
            resp = await s3.get_object(**params)
            async for chunk in resp["Body"].iter_chunks(chunk_size=65_536):
                yield chunk

    async def get_object_bytes(self, bucket: str, key: str) -> bytes:
        """Download the full object into memory (use only for small objects).

        For large objects prefer :meth:`get_object_stream`.
        """
        chunks: list[bytes] = []
        async for chunk in self.get_object_stream(bucket, key):
            chunks.append(chunk)
        return b"".join(chunks)


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------
def get_minio_service() -> MinIOService:
    """Return the global :class:`MinIOService` singleton."""
    return MinIOService()
