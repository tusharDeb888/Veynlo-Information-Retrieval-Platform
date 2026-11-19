# Multi-Tenant RAG Knowledge Assurance Platform

A FastAPI service for tenant-isolated document ingestion and retrieval. Files
are uploaded directly to MinIO with presigned multipart URLs, indexed by Celery
workers into Qdrant, and queried through an ACL-filtered retrieval pipeline.

## Architecture

| Component | Responsibility |
| --- | --- |
| FastAPI | Authentication, upload/permission APIs, and grounded query responses |
| PostgreSQL | Tenant metadata, document state, permissions, and RLS enforcement |
| Redis | Celery broker/result backend and ACL cache |
| MinIO | Source document storage and multipart uploads |
| Qdrant Cloud | Tenant- and ACL-filtered chunk retrieval |
| Celery worker / beat | Ingestion, retry, and stalled-document recovery |

## Prerequisites

- Docker Desktop with the Compose plugin
- Python 3.12+ for local development (the containers use Python 3.12)
- A Qdrant Cloud cluster endpoint and a Database API key with manage/write access
- A Groq API key for grounded answer synthesis
- At least 6 GB RAM available to Docker; embedding and reranking models are
  downloaded on their first use

## Configure and run the stack

```bash
cd rag-knowledge-assurance
cp .env.example .env
# Edit .env: set QDRANT_URL, QDRANT_API_KEY, GROQ_API_KEY, and non-default secrets.
docker compose up --build -d postgres redis minio minio-init
docker compose run --rm fastapi alembic upgrade head
docker compose up --build -d fastapi celery-worker celery-beat
docker compose ps
curl -fsS http://localhost:8000/health
```

The first worker startup can take several minutes while model weights are
downloaded. Follow it with `docker compose logs -f celery-worker`.

The default `MINIO_IMAGE` is a pinned, byte-identical mirror of the last
public MinIO release because upstream public images were withdrawn. Review and
override it with your approved private mirror for production deployments.

For a clean teardown that also removes persisted development data, use
`docker compose down --volumes`.

## Authentication

For local development set `AUTH_MODE=local_jwt`, replace `JWT_SECRET_KEY`,
and create a tenant, department, and user in PostgreSQL. The JWT `sub` claim
must equal that user's `firebase_uid` value. The user must be granted either
`TENANT_ADMIN` or `DEPT_ADMIN` to initiate uploads or modify permissions.

For Firebase, set `AUTH_MODE=firebase`, `FIREBASE_PROJECT_ID`, and
`GOOGLE_APPLICATION_CREDENTIALS`. Mount the service-account JSON into the API,
worker, and beat containers at the configured path; keep that file out of Git.

## Qdrant Cloud collection and indexes

Do **not** create the collection manually. At API startup the service connects
to `QDRANT_URL`, creates `QDRANT_COLLECTION` (`knowledge_chunks` by default),
and creates these payload indexes idempotently:

| Payload field | Schema | Reason |
| --- | --- | --- |
| `tenant_id` | keyword / tenant | hard tenant boundary |
| `file_id` | keyword | document deletion and permission propagation |
| `allowed_departments` | keyword | department ACL filter |
| `allowed_roles` | keyword | role ACL filter |
| `allowed_users` | keyword | explicit-grant ACL filter |

The collection uses named vectors `dense` (384 dimensions, cosine) and `bm25`
(sparse with IDF) for hybrid retrieval. Do not point `QDRANT_COLLECTION` at a
collection using the screenshot's 3072-dimensional `dense-vector` schema: it
does not match this application's 384-dimensional embedding model. Use a new
collection name if that existing collection contains any data you need to keep.

In the Qdrant UI, select **Multitenancy** and **Simple Hybrid Search** only if
you intentionally create the collection yourself. Use `dense` as the dense
vector name, `384` dimensions, cosine distance, `bm25` as the sparse vector
name, and enable IDF. Then add the five keyword payload indexes above.

## LLM modes

Set `LLM_PROVIDER=groq`, `GROQ_API_KEY`, and optionally `GROQ_MODEL`.
`mock` remains available for credential-free unit tests, but the Compose stack
defaults to Groq and returns a clear configuration error if its key is absent.

## Verification

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -m unit
.venv/bin/ruff check app tests
docker compose config --quiet
```

The unit suite uses fakes and does not require Docker. Integration tests are
reserved for a running stack and should validate PostgreSQL RLS, MinIO
multipart completion, Qdrant ACL filters, and ingestion crash recovery.

## Operational notes

- Do not deploy with the example credentials or wildcard CORS settings.
- Run `alembic upgrade head` before starting application services after a new
  migration is introduced.
- Back up PostgreSQL, MinIO, and Redis volumes. Configure Qdrant Cloud backups
  and retention in the Qdrant Cloud console.
- Qdrant is a second enforcement layer; PostgreSQL RLS remains the source of
  truth for document and permission metadata.
# Veynlo-Information-Retrieval-Platform
