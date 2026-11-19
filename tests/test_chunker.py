from __future__ import annotations

from app.workers.chunker import fast_forward_chunks, stream_chunks


class _Body:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    def iter_chunks(self, chunk_size: int):  # noqa: ARG002
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


class _S3:
    def __init__(self, body: _Body) -> None:
        self.body = body
        self.request: dict[str, str] | None = None

    def get_object(self, **kwargs: str) -> dict[str, _Body]:
        self.request = kwargs
        return {"Body": self.body}


def test_text_chunking_is_sequential_and_preserves_overlap() -> None:
    body = _Body([b"one two three four five six seven eight nine ten"])
    s3 = _S3(body)

    chunks = list(
        stream_chunks(
            s3,
            "knowledge-vault",
            "tenants/a/example.txt",
            "text/plain",
            "example.txt",
            target_tokens=4,
            overlap_tokens=1,
        )
    )

    assert s3.request == {"Bucket": "knowledge-vault", "Key": "tenants/a/example.txt"}
    assert body.closed is True
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.filename == "example.txt" for chunk in chunks)
    assert chunks[0].text == "one two three four"
    assert chunks[1].text == "four five six seven"


def test_fast_forward_skips_committed_chunk_indexes() -> None:
    body = _Body([b"one two three four five six seven eight nine ten"])
    source = stream_chunks(
        _S3(body),
        "knowledge-vault",
        "document.txt",
        "text/plain",
        "document.txt",
        target_tokens=3,
        overlap_tokens=1,
    )

    remaining = list(fast_forward_chunks(source, last_completed_chunk=1))

    assert remaining
    assert remaining[0].index == 2
    assert all(chunk.index > 1 for chunk in remaining)
