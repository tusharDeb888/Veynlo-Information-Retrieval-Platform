"""FastAPI router – RAG hybrid search + cross-encoder reranking + LLM synthesis.

Endpoint:
  POST   /api/v1/query  – employee knowledge-base Q&A
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue, MinShould

from app.core.security import require_authenticated_employee
from app.schemas.query import ChunkResult, QueryRequest, QueryResponse
from app.services.embedding_service import EmbeddingService, build_sparse_vector
from app.services.llm_service import LLMService
from app.services.qdrant_service import QdrantService
from app.services.reranker_service import RerankerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/query", tags=["query"])

# ---------------------------------------------------------------------------
# Service singletons (lazy init)
# ---------------------------------------------------------------------------
_qdrant: QdrantService | None = None
_reranker: RerankerService | None = None
_llm: LLMService | None = None
_embedder: EmbeddingService | None = None


def _get_qdrant() -> QdrantService:
    global _qdrant  # noqa: PLW0603
    if _qdrant is None:
        _qdrant = QdrantService()
    return _qdrant


def _get_reranker() -> RerankerService:
    global _reranker  # noqa: PLW0603
    if _reranker is None:
        _reranker = RerankerService()
    return _reranker


def _get_llm() -> LLMService:
    global _llm  # noqa: PLW0603
    if _llm is None:
        _llm = LLMService()
    return _llm


def _get_embedder() -> EmbeddingService:
    global _embedder  # noqa: PLW0603
    if _embedder is None:
        _embedder = EmbeddingService()
    return _embedder


# ---------------------------------------------------------------------------
# ACL filter builder
# ---------------------------------------------------------------------------
def _build_acl_filter(user: object) -> Filter:
    """Build the mandatory tenant and ABAC filter for a Qdrant query.

    The filter enforces:
      MUST  – tenant_id matches the user's tenant
      SHOULD (min 1) – user's department, role, or explicit user id is allowed

    ``min_should`` is intentional: a matching tenant alone is never enough;
    a chunk must also be granted to the caller's department, role, or user ID.
    """
    return Filter(
        must=[
            FieldCondition(
                key="tenant_id",
                match=MatchValue(value=str(user.tenant_id)),
            ),
            FieldCondition(
                key="file_id",
                match=MatchAny(any=[str(document_id) for document_id in user.allowed_document_ids]),
            ),
        ],
        min_should=MinShould(
            conditions=[
                FieldCondition(
                    key="allowed_departments",
                    match=MatchValue(value=str(user.department_id)),
                ),
                FieldCondition(
                    key="allowed_roles",
                    match=MatchValue(value=user.role),
                ),
                FieldCondition(
                    key="allowed_users",
                    match=MatchValue(value=str(user.user_id)),
                ),
            ],
            min_count=1,
        ),
    )


# ---------------------------------------------------------------------------
# POST /api/v1/query
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=QueryResponse,
    summary="Query the knowledge base",
)
async def query_knowledge_base(
    body: QueryRequest,
    current_user=Depends(require_authenticated_employee),
) -> QueryResponse:
    """Execute a full RAG pipeline: hybrid search → rerank → LLM synthesis.

    Steps:
    1. Build a strict ACL filter from the authenticated user's identity.
    2. Qdrant hybrid search (dense Prefetch + BM25 Prefetch → Fusion.RRF)
       with the ACL filter, returning up to 50 candidates.
    3. Cross-encoder reranking of the 50 candidates, keeping top-k.
    4. LLM grounded answer synthesis using only the reranked chunks.
    """
    qdrant = _get_qdrant()
    reranker = _get_reranker()
    llm = _get_llm()
    embedder = _get_embedder()

    acl_filter = _build_acl_filter(current_user)

    if not current_user.allowed_document_ids:
        return QueryResponse(
            answer="No relevant documents found for your query within your authorised scope.",
            citations=[],
            model_used=llm.model_name,
        )

    # ------------------------------------------------------------------
    # 1. Hybrid retrieval (dense + BM25 → RRF fusion)
    # ------------------------------------------------------------------
    try:
        # SentenceTransformer and the synchronous Qdrant client both perform
        # blocking work, so execute them off FastAPI's event loop.
        query_embedding = await asyncio.to_thread(embedder.embed_one, body.question)
        query_sparse_vector = build_sparse_vector(body.question)
        scored_points = await asyncio.to_thread(
            qdrant.hybrid_search,
            query_embedding,
            query_sparse_vector,
            acl_filter,
            50,
        )
    except Exception as exc:
        logger.exception("Qdrant hybrid search failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge search is temporarily unavailable.",
        ) from exc

    if not scored_points:
        return QueryResponse(
            answer="No relevant documents found for your query within your authorised scope.",
            citations=[],
            model_used=llm.model_name,
        )

    # ------------------------------------------------------------------
    # 2. Cross-encoder reranking (CPU-bound → run in thread)
    # ------------------------------------------------------------------
    try:
        ranked_chunks = await asyncio.to_thread(
            reranker.rerank,
            body.question,
            scored_points,
            body.top_k,
        )
    except Exception:
        logger.exception("Reranker failed; falling back to fusion-ranked order")
        # Graceful degradation: use the first top_k results as-is
        ranked_chunks = []
        for sp in scored_points[: body.top_k]:
            payload = sp.payload or {}
            ranked_chunks.append(
                type(
                    "RankedChunk",
                    (),
                    {
                        "file_id": payload.get("file_id", ""),
                        "filename": payload.get("filename", ""),
                        "page_number": payload.get("page_number"),
                        "chunk_index": payload.get("chunk_index", 0),
                        "text": payload.get("text", ""),
                        "rerank_score": float(sp.score) if sp.score else 0.0,
                    },
                )()
            )

    if not ranked_chunks:
        return QueryResponse(
            answer="No relevant documents found for your query within your authorised scope.",
            citations=[],
            model_used=llm.model_name,
        )

    # ------------------------------------------------------------------
    # 3. Build citations for the response
    # ------------------------------------------------------------------
    citations: list[ChunkResult] = []
    for rc in ranked_chunks:
        citations.append(
            ChunkResult(
                file_id=UUID(rc.file_id) if isinstance(rc.file_id, str) else rc.file_id,
                filename=rc.filename,
                page_number=rc.page_number,
                chunk_index=rc.chunk_index or 0,
                text=rc.text,
                rerank_score=rc.rerank_score,
            )
        )

    # ------------------------------------------------------------------
    # 4. LLM grounded answer synthesis
    # ------------------------------------------------------------------
    try:
        grounded_answer = await llm.generate_answer(body.question, ranked_chunks)
    except Exception as exc:
        logger.exception("LLM synthesis failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Answer synthesis is temporarily unavailable.",
        ) from exc

    return QueryResponse(
        answer=grounded_answer.answer,
        citations=citations,
        model_used=grounded_answer.model,
    )
