from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.query import QueryRequest
from app.schemas.upload import CompleteUploadRequest, FileManifest


def test_file_manifest_calculates_multipart_parts() -> None:
    manifest = FileManifest(
        filename="handbook.pdf",
        size_bytes=25 * 1024 * 1024 + 1,
        sha256_hash="a" * 64,
        content_type="application/pdf",
        allowed_user_ids=[uuid4()],
    )

    assert manifest.num_parts == 2


def test_complete_upload_parts_are_sorted_and_must_be_contiguous() -> None:
    payload = CompleteUploadRequest(
        parts=[
            {"PartNumber": 2, "ETag": "etag-2"},
            {"PartNumber": 1, "ETag": "etag-1"},
        ]
    )

    assert [part.PartNumber for part in payload.parts] == [1, 2]

    with pytest.raises(ValidationError, match="consecutively numbered"):
        CompleteUploadRequest(parts=[{"PartNumber": 2, "ETag": "etag-2"}])


def test_query_request_rejects_empty_questions_and_out_of_range_top_k() -> None:
    with pytest.raises(ValidationError):
        QueryRequest(question="")
    with pytest.raises(ValidationError):
        QueryRequest(question="What is our policy?", top_k=51)
