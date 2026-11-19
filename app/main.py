"""FastAPI application entry-point.

Configures:
- Lifespan handler (Qdrant collection init, model pre-download)
- CORS middleware
- v1 routers (uploads, permissions, query)
- Health endpoint
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.permissions import router as permissions_router
from app.api.v1.query import router as query_router
from app.api.v1.uploads import router as uploads_router

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    """Startup / shutdown lifecycle handler.

    On startup:
      1. Initialise the Qdrant ``knowledge_chunks`` collection + indexes.
      2. Pre-download cross-encoder model weights so the first query isn't slow.
    On shutdown:
      Nothing special – services clean up after themselves.
    """
    logger.info("Starting Multi-Tenant RAG Knowledge Assurance Platform …")

    # --- Qdrant collection bootstrap ---
    try:
        from app.services.qdrant_service import QdrantService

        qdrant = QdrantService()
        await asyncio.to_thread(qdrant.init_collection)
        logger.info("Qdrant collection initialised.")
    except Exception:
        logger.exception("Qdrant collection init failed — search will not work until resolved.")

    # Reranker and embedding weights load lazily on the first query/ingestion.
    # This keeps readiness independent of a potentially slow Hugging Face pull.
    logger.info("ML models will load lazily on their first use.")

    yield  # --- application serves requests ---

    logger.info("Shutting down …")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    application = FastAPI(
        title="Multi-Tenant RAG Knowledge Assurance Platform",
        version="1.0.0",
        description=(
            "Production-grade RAG platform with presigned multipart uploads, "
            "PostgreSQL RLS multi-tenancy, Qdrant hybrid search with cross-encoder "
            "reranking, and grounded LLM answer synthesis."
        ),
        lifespan=lifespan,
    )

    # --- CORS ---
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # tighten in production via env
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Routers ---
    application.include_router(uploads_router)
    application.include_router(permissions_router)
    application.include_router(query_router)

    # --- Health endpoint ---
    @application.get(
        "/health",
        status_code=status.HTTP_200_OK,
        tags=["health"],
        summary="Liveness / readiness probe",
    )
    async def health() -> JSONResponse:
        return JSONResponse(content={"status": "healthy"})

    return application


app = create_app()
