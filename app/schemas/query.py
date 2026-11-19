"""Pydantic v2 schemas for hybrid search + cross-encoder reranked RAG query."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    """Employee query against the RAG knowledge base."""

    model_config = ConfigDict(strict=True)

    question: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Natural-language question.",
    )
    top_k: int = Field(
        default=8,
        ge=1,
        le=50,
        description="Number of top reranked chunks to include in the answer synthesis.",
    )


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
class ChunkResult(BaseModel):
    """A single retrieved and reranked chunk citation."""

    file_id: UUID
    filename: str
    page_number: int | None = Field(
        default=None,
        description="Source page number (1-based) if applicable.",
    )
    chunk_index: int = Field(
        ...,
        ge=0,
        description="Sequential chunk index within the source document.",
    )
    text: str = Field(
        ...,
        description="Chunk text content.",
    )
    rerank_score: float = Field(
        ...,
        description="Cross-encoder reranking score.",
    )


class QueryResponse(BaseModel):
    """Grounded answer with citations from authorised documents only."""

    answer: str = Field(
        ...,
        description="LLM-synthesised answer strictly grounded in the citations.",
    )
    citations: list[ChunkResult] = Field(
        default_factory=list,
        description="Top-k reranked chunk citations backing the answer.",
    )
    model_used: str = Field(
        ...,
        description="Identifier of the LLM model used for answer synthesis.",
    )
