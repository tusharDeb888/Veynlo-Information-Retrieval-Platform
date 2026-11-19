"""
Cross-Encoder reranking service.

Loads a Sentence-Transformers ``CrossEncoder`` model once at import time
(singleton) and exposes a ``rerank`` method that scores ``[query, passage]``
pairs in a single batched ``predict()`` call.

The model is CPU-bound – call ``rerank`` via ``asyncio.to_thread`` from
async FastAPI handlers so the event loop is never blocked.

References
----------
- Sentence-Transformers Cross-Encoder usage:
  https://sbert.net/docs/cross_encoder/usage/usage.html
- Retrieve & Re-Rank pipeline:
  https://sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sentence_transformers import CrossEncoder

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DEFAULT_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_MODEL_NAME: str = get_settings().CROSS_ENCODER_MODEL or _DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Ranked-chunk data class
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RankedChunk:
    """A single reranked chunk returned to the caller."""

    file_id: str
    filename: str
    page_number: int | None
    text: str
    chunk_index: int | None
    rerank_score: float


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class RerankerService:
    """Singleton cross-encoder reranking service.

    The model is loaded lazily on first access, keeping cold-start fast when
    the service object is created but not yet used.
    """

    _instance: RerankerService | None = None
    _model: CrossEncoder | None = None

    def __new__(cls) -> RerankerService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ------------------------------------------------------------------
    # Lazy model loading
    # ------------------------------------------------------------------
    @classmethod
    def _get_model(cls) -> CrossEncoder:
        if cls._model is None:
            logger.info("Loading CrossEncoder model '%s' …", _MODEL_NAME)
            cls._model = CrossEncoder(_MODEL_NAME)
            logger.info("CrossEncoder model loaded successfully.")
        return cls._model

    @classmethod
    def warmup(cls) -> None:
        """Pre-load the model (call during app lifespan startup)."""
        cls._get_model()

    # ------------------------------------------------------------------
    # Reranking
    # ------------------------------------------------------------------
    def rerank(
        self,
        query: str,
        hits: list[Any],
        top_k: int = 8,
    ) -> list[RankedChunk]:
        """Score and rerank retrieved hits against *query*.

        Parameters
        ----------
        query:
            The user's natural-language query.
        hits:
            Qdrant ``ScoredPoint`` objects (or any object whose ``.payload``
            dict contains ``text``, ``file_id``, ``filename``, and
            optionally ``page_number`` and ``chunk_index``).
        top_k:
            Number of top-scoring chunks to return.

        Returns
        -------
        list[RankedChunk]
            Reranked chunks sorted descending by cross-encoder score.

        Notes
        -----
        This method is **CPU-bound**.  Callers in async contexts should
        invoke it via ``await asyncio.to_thread(reranker.rerank, ...)``.
        """
        if not hits:
            return []

        model = self._get_model()

        # Build sentence pairs ------------------------------------------------
        pairs: list[list[str]] = []
        valid_indices: list[int] = []
        for idx, hit in enumerate(hits):
            payload: dict[str, Any] = hit.payload if hasattr(hit, "payload") else hit
            text = payload.get("text", "")
            if text:
                pairs.append([query, text])
                valid_indices.append(idx)

        if not pairs:
            return []

        # Batched inference ----------------------------------------------------
        scores = model.predict(pairs)

        # Combine scores with metadata ----------------------------------------
        scored_hits: list[tuple[float, int]] = sorted(
            zip(scores, valid_indices, strict=True),
            key=lambda t: t[0],
            reverse=True,
        )

        results: list[RankedChunk] = []
        for score, idx in scored_hits[:top_k]:
            payload = hits[idx].payload if hasattr(hits[idx], "payload") else hits[idx]
            results.append(
                RankedChunk(
                    file_id=str(payload.get("file_id", "")),
                    filename=str(payload.get("filename", "")),
                    page_number=payload.get("page_number"),
                    text=str(payload.get("text", "")),
                    chunk_index=payload.get("chunk_index"),
                    rerank_score=float(score),
                )
            )

        logger.info(
            "Reranked %d hits → top %d (scores %.4f … %.4f).",
            len(pairs),
            len(results),
            results[0].rerank_score if results else 0.0,
            results[-1].rerank_score if results else 0.0,
        )
        return results


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------
def get_reranker_service() -> RerankerService:
    """Return the global :class:`RerankerService` singleton."""
    return RerankerService()
