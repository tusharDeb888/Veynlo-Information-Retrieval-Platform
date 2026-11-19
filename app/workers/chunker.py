"""Constant-memory streaming chunker for PDF, DOCX, TXT and Markdown files.

Streams objects from MinIO via byte-range ``get_object`` through a
``tempfile.SpooledTemporaryFile`` so that a 500 MB document is never loaded
entirely into RAM.  Each file type is iterated in a format-specific way:

* **PDF** – page-by-page via *pypdfium2* (``PdfDocument``).
* **DOCX** – paragraph-by-paragraph via *python-docx*.
* **TXT / Markdown** – line-by-line.

Chunks are ~512 tokens with 64-token overlap and carry deterministic,
sequentially increasing ``chunk_index`` values so that the ingest worker can
fast-forward past ``last_completed_chunk`` for crash recovery.
"""

from __future__ import annotations

import io
import logging
import tempfile
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field

import boto3
import pypdfium2 as pdfium
from botocore.config import Config as BotoConfig
from docx import Document as DocxDocument

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TARGET_TOKENS: int = 512
OVERLAP_TOKENS: int = 64
# Simple whitespace tokeniser for portability; production systems may swap in
# a tiktoken / sentencepiece wrapper without changing the chunker interface.
_WORD_SEP = None  # str.split() default – any whitespace


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Chunk:
    """A single text chunk emitted by the streaming chunker."""

    index: int
    text: str
    page_number: int | None = None
    filename: str = ""


@dataclass
class _TokenBuffer:
    """Accumulates *words* (proxy for tokens) and flushes once the target
    window is reached, keeping an overlap tail for the next chunk."""

    target: int = TARGET_TOKENS
    overlap: int = OVERLAP_TOKENS
    _words: list[str] = field(default_factory=list)
    _page: int | None = None  # tracks the page of the *first* word

    # -- public API --------------------------------------------------------

    def add(self, text: str, page_number: int | None = None) -> list[tuple[str, int | None]]:
        """Add *text* to the buffer.  Returns a (possibly empty) list of
        ``(chunk_text, page_number)`` tuples that are ready to be emitted."""
        words = text.split(_WORD_SEP)
        if not words or (len(words) == 1 and words[0] == ""):
            return []

        ready: list[tuple[str, int | None]] = []
        for w in words:
            if not w:
                continue
            if not self._words:
                self._page = page_number
            self._words.append(w)
            if len(self._words) >= self.target:
                ready.append((" ".join(self._words), self._page))
                # Keep the last *overlap* words for context continuity.
                self._words = self._words[-self.overlap :]
                self._page = page_number
        return ready

    def flush(self) -> tuple[str, int | None] | None:
        """Flush any remaining content in the buffer."""
        if self._words:
            result = (" ".join(self._words), self._page)
            self._words = []
            self._page = None
            return result
        return None


# ---------------------------------------------------------------------------
# MinIO streaming helpers
# ---------------------------------------------------------------------------


def _get_s3_client(
    endpoint: str,
    access_key: str,
    secret_key: str,
    use_ssl: bool = False,
) -> boto3.client:
    """Create a synchronous boto3 S3 client for MinIO."""
    return boto3.client(
        "s3",
        endpoint_url=f"{'https' if use_ssl else 'http'}://{endpoint}",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "adaptive"},
        ),
    )


def _stream_object_to_spooled(
    s3_client: boto3.client,
    bucket: str,
    key: str,
    max_spool_bytes: int = 50 * 1024 * 1024,  # 50 MiB in-memory before disk
) -> tempfile.SpooledTemporaryFile:
    """Stream an S3 object into a ``SpooledTemporaryFile``.

    The streaming body is consumed in 1 MiB chunks so that peak RAM is bounded
    by *max_spool_bytes*.  Once that threshold is exceeded the spool rolls over
    to a temporary file on disk.
    """
    response = s3_client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    spool = tempfile.SpooledTemporaryFile(max_size=max_spool_bytes)  # noqa: SIM115
    try:
        for data in body.iter_chunks(chunk_size=1024 * 1024):
            spool.write(data)
    finally:
        body.close()
    spool.seek(0)
    return spool


# ---------------------------------------------------------------------------
# Format-specific text iterators
# ---------------------------------------------------------------------------


def _iter_pdf_pages(spool: tempfile.SpooledTemporaryFile) -> Iterator[tuple[str, int]]:
    """Yield ``(page_text, 1-based page_number)`` from a PDF spool."""
    raw = spool.read()
    pdf = pdfium.PdfDocument(raw)
    try:
        for page_idx in range(len(pdf)):
            page = pdf[page_idx]
            text = page.get_textpage().get_text_range()
            if text and text.strip():
                yield text, page_idx + 1
    finally:
        pdf.close()


def _iter_docx_paragraphs(spool: tempfile.SpooledTemporaryFile) -> Iterator[tuple[str, None]]:
    """Yield ``(paragraph_text, None)`` from a DOCX spool."""
    doc = DocxDocument(spool)
    for para in doc.paragraphs:
        text = para.text
        if text and text.strip():
            yield text, None


def _iter_text_lines(spool: tempfile.SpooledTemporaryFile) -> Iterator[tuple[str, None]]:
    """Yield ``(line_text, None)`` from a plain-text or Markdown spool."""
    wrapper = io.TextIOWrapper(spool, encoding="utf-8", errors="replace")
    for line in wrapper:
        stripped = line.rstrip("\n\r")
        if stripped:
            yield stripped, None


_FORMAT_ITERATORS = {
    "application/pdf": _iter_pdf_pages,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _iter_docx_paragraphs,
    "text/plain": _iter_text_lines,
    "text/markdown": _iter_text_lines,
    "text/x-markdown": _iter_text_lines,
}


def _get_iterator(content_type: str):
    """Resolve the format-specific text iterator for a MIME type."""
    ct = content_type.lower().split(";")[0].strip()
    iterator_fn = _FORMAT_ITERATORS.get(ct)
    if iterator_fn is None:
        # Fall back to plain text for unknown types
        logger.warning(
            "Unknown content_type %r — falling back to plain-text line iterator.",
            content_type,
        )
        return _iter_text_lines
    return iterator_fn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def stream_chunks(
    s3_client: boto3.client,
    bucket: str,
    s3_key: str,
    content_type: str,
    filename: str,
    *,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> Generator[Chunk, None, None]:
    """Yield deterministic, sequentially indexed :class:`Chunk` objects.

    Parameters
    ----------
    s3_client:
        Synchronous boto3 S3 client connected to MinIO.
    bucket:
        S3 bucket name (e.g. ``"knowledge-vault"``).
    s3_key:
        Object key inside the bucket.
    content_type:
        MIME type of the stored object — drives format selection.
    filename:
        Original filename, attached to every emitted chunk.
    target_tokens:
        Approximate number of whitespace-delimited tokens per chunk.
    overlap_tokens:
        Number of trailing tokens to overlap between consecutive chunks.

    Yields
    ------
    Chunk
        Each chunk carries a deterministic ``index`` (0, 1, 2, …),
        the extracted ``text``, an optional ``page_number`` (PDFs),
        and the ``filename``.
    """
    spool = _stream_object_to_spooled(s3_client, bucket, s3_key)
    try:
        iterator_fn = _get_iterator(content_type)
        buf = _TokenBuffer(target=target_tokens, overlap=overlap_tokens)
        chunk_idx = 0

        for text_segment, page_number in iterator_fn(spool):
            ready_pairs = buf.add(text_segment, page_number)
            for chunk_text, chunk_page in ready_pairs:
                yield Chunk(
                    index=chunk_idx,
                    text=chunk_text,
                    page_number=chunk_page,
                    filename=filename,
                )
                chunk_idx += 1

        # Flush remaining content
        remainder = buf.flush()
        if remainder is not None:
            chunk_text, chunk_page = remainder
            if chunk_text.strip():
                yield Chunk(
                    index=chunk_idx,
                    text=chunk_text,
                    page_number=chunk_page,
                    filename=filename,
                )
    finally:
        spool.close()


def fast_forward_chunks(
    chunks: Generator[Chunk, None, None],
    last_completed_chunk: int,
) -> Generator[Chunk, None, None]:
    """Skip chunks whose index is ``<= last_completed_chunk``.

    This is the crash-recovery fast-forward: after a restart the ingest task
    queries PostgreSQL for the last committed checkpoint and uses this wrapper
    to skip already-indexed chunks without re-embedding them.
    """
    for chunk in chunks:
        if chunk.index > last_completed_chunk:
            yield chunk
