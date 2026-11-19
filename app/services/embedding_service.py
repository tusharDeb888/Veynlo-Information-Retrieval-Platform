"""Shared dense and sparse query-vector construction.

The worker and query API must produce vectors with exactly the same sparse
token hashing scheme.  Keeping it here prevents retrieval from silently
diverging from ingestion after a future change.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter

from qdrant_client.models import SparseVector
from sentence_transformers import SentenceTransformer

from app.core.config import get_settings

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def build_sparse_vector(text: str) -> SparseVector:
    """Create a deterministic bag-of-words sparse vector for Qdrant BM25."""
    counts = Counter(_TOKEN_RE.findall(text.lower()))
    by_index: dict[int, float] = {}
    for token, count in counts.items():
        index = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(), "big")
        by_index[index] = by_index.get(index, 0.0) + float(count)
    indices = sorted(by_index)
    return SparseVector(indices=indices, values=[by_index[index] for index in indices])


class EmbeddingService:
    """Lazily loaded SentenceTransformer used by ingestion and retrieval."""

    _instance: EmbeddingService | None = None
    _model: SentenceTransformer | None = None

    def __new__(cls) -> EmbeddingService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def _get_model(cls) -> SentenceTransformer:
        if cls._model is None:
            model_name = get_settings().EMBEDDING_MODEL
            logger.info("Loading embedding model '%s' …", model_name)
            cls._model = SentenceTransformer(model_name)
        return cls._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Encode text into normalised dense vectors suitable for cosine search."""
        if not texts:
            return []
        vectors = self._get_model().encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Compatibility name used by the ingestion worker."""
        return self.embed(texts)

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
