"""
Qdrant vector-database service.

Manages the ``knowledge_chunks`` collection with:
- Named dense vector (384-dim, cosine, on-disk)
- Named sparse vector ``bm25`` (IDF modifier)
- Payload-based multitenancy (``tenant_id`` with ``is_tenant=True``)
- Keyword payload indexes for sub-millisecond ACL pre-filtering
- Hybrid retrieval via two Prefetch branches fused with Reciprocal Rank Fusion

References
----------
- Qdrant multitenancy:  https://qdrant.tech/documentation/manage-data/multitenancy/
- Qdrant hybrid search: https://qdrant.tech/documentation/search-tuning/hybrid-search/
- Qdrant indexing:       https://qdrant.tech/documentation/manage-data/indexing/
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client import QdrantClient, models
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    KeywordIndexParams,
    MatchValue,
    Modifier,
    PayloadSchemaType,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DENSE_VECTOR_NAME: str = "dense"
SPARSE_VECTOR_NAME: str = "bm25"


class QdrantService:
    """Singleton wrapper around :class:`qdrant_client.QdrantClient`."""

    _instance: QdrantService | None = None

    def __new__(cls) -> QdrantService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialised = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialised:  # type: ignore[has-type]
            return
        client_kwargs: dict[str, Any] = {
            # HTTP(S) is universally available in Qdrant Cloud and avoids an
            # optional grpcio runtime dependency in the API containers.
            "prefer_grpc": False,
            "timeout": 60,
        }
        if settings.QDRANT_URL:
            client_kwargs.update(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY or None,
            )
            logger.info("Connecting to Qdrant using the configured HTTPS endpoint.")
        else:
            client_kwargs.update(
                host=settings.QDRANT_HOST,
                port=settings.QDRANT_PORT,
                grpc_port=settings.QDRANT_GRPC_PORT,
            )
            logger.warning("QDRANT_URL is unset; using the local Qdrant fallback.")

        self._client = QdrantClient(**client_kwargs)
        self._initialised = True

    @property
    def client(self) -> QdrantClient:
        return self._client

    # ------------------------------------------------------------------
    # Collection bootstrap
    # ------------------------------------------------------------------
    def init_collection(self) -> None:
        """Create the ``knowledge_chunks`` collection idempotently.

        Sets up:
        - Dense named vector ``dense`` (384-d, cosine, on-disk)
        - Sparse named vector ``bm25`` with IDF modifier
        - Payload keyword indexes for tenant isolation and ACL filtering
        """
        collection_name = settings.QDRANT_COLLECTION
        collections = [c.name for c in self._client.get_collections().collections]
        if collection_name in collections:
            logger.info("Collection '%s' already exists – skipping creation.", collection_name)
        else:
            self._client.create_collection(
                collection_name=collection_name,
                vectors_config={
                    DENSE_VECTOR_NAME: VectorParams(
                        size=settings.EMBEDDING_DIMENSION,
                        distance=Distance.COSINE,
                        on_disk=True,
                    ),
                },
                sparse_vectors_config={
                    SPARSE_VECTOR_NAME: SparseVectorParams(
                        modifier=Modifier.IDF,
                    ),
                },
            )
            logger.info("Created collection '%s'.", collection_name)

        # Payload indexes (idempotent – Qdrant ignores if already present)
        index_fields: list[tuple[str, dict[str, Any]]] = [
            (
                "tenant_id",
                {
                    "field_schema": KeywordIndexParams(
                        type=PayloadSchemaType.KEYWORD,
                        is_tenant=True,
                    )
                },
            ),
            ("file_id", {"field_schema": PayloadSchemaType.KEYWORD}),
            ("allowed_departments", {"field_schema": PayloadSchemaType.KEYWORD}),
            ("allowed_roles", {"field_schema": PayloadSchemaType.KEYWORD}),
            ("allowed_users", {"field_schema": PayloadSchemaType.KEYWORD}),
        ]

        for field_name, kwargs in index_fields:
            try:
                self._client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    **kwargs,
                )
                logger.info("Created payload index on '%s'.", field_name)
            except Exception:
                # Qdrant may raise if index already exists – safe to ignore
                logger.debug("Payload index '%s' likely already exists.", field_name)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------
    def upsert_points(self, points: list[PointStruct]) -> None:
        """Batch upsert points with ``wait=True`` for crash consistency.

        Parameters
        ----------
        points:
            List of :class:`PointStruct` to upsert into the collection.
        """
        if not points:
            return
        self._client.upsert(
            collection_name=settings.QDRANT_COLLECTION,
            points=points,
            wait=True,
        )
        logger.info("Upserted %d points into '%s'.", len(points), settings.QDRANT_COLLECTION)

    def set_payload(
        self,
        file_id: str,
        payload_updates: dict[str, Any],
    ) -> None:
        """Update payload fields on **all** points matching *file_id*.

        Used when a Departmental Admin changes ACL on a document after ingestion.

        Parameters
        ----------
        file_id:
            The document file identifier stored in each point's payload.
        payload_updates:
            Dict of payload keys/values to set, e.g.
            ``{"allowed_departments": [...], "allowed_roles": [...]}``.
        """
        points_filter = Filter(
            must=[
                FieldCondition(
                    key="file_id",
                    match=MatchValue(value=file_id),
                )
            ]
        )
        self._client.set_payload(
            collection_name=settings.QDRANT_COLLECTION,
            payload=payload_updates,
            points=points_filter,
            wait=True,
        )
        logger.info("Updated payload for file_id=%s.", file_id)

    def delete_points_by_file(self, file_id: str) -> None:
        """Delete **all** points belonging to a given *file_id*.

        Parameters
        ----------
        file_id:
            The document file identifier.
        """
        points_filter = Filter(
            must=[
                FieldCondition(
                    key="file_id",
                    match=MatchValue(value=file_id),
                )
            ]
        )
        self._client.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=points_filter,
            wait=True,
        )
        logger.info("Deleted all points for file_id=%s.", file_id)

    # ------------------------------------------------------------------
    # Hybrid search
    # ------------------------------------------------------------------
    def hybrid_search(
        self,
        query_embedding: list[float],
        query_sparse_vector: SparseVector,
        acl_filter: Filter,
        limit: int = 50,
    ) -> list[models.ScoredPoint]:
        """Execute a hybrid (dense + BM25) search with ACL pre-filtering.

        Uses two :class:`Prefetch` branches fused via Reciprocal Rank Fusion
        (``Fusion.RRF``), following the Qdrant hybrid-search best practice.

        Parameters
        ----------
        query_embedding:
            Dense vector for semantic similarity (384-d).
        query_sparse_vector:
            Pre-computed BM25 sparse vector for the query.
        acl_filter:
            Mandatory :class:`Filter` enforcing tenant isolation and
            user-level ACL (department, role, explicit user grants).
        limit:
            Maximum number of fused results to return.

        Returns
        -------
        list[ScoredPoint]
            Scored points ordered by RRF score descending.
        """
        results = self._client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            prefetch=[
                Prefetch(
                    query=query_embedding,
                    using=DENSE_VECTOR_NAME,
                    filter=acl_filter,
                    limit=limit,
                ),
                Prefetch(
                    query=query_sparse_vector,
                    using=SPARSE_VECTOR_NAME,
                    filter=acl_filter,
                    limit=limit,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=limit,
            with_payload=True,
        )
        logger.info("Hybrid search returned %d candidates.", len(results.points))
        return results.points


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------
def get_qdrant_service() -> QdrantService:
    """Return the global :class:`QdrantService` singleton."""
    return QdrantService()
