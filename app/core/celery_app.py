"""
Celery application configured for production reliability.

Key settings (per Celery Task Reliability Best Practices):
* ``task_acks_late`` – message is acknowledged only after the task returns,
  so a killed worker causes the message to be redelivered.
* ``task_reject_on_worker_lost`` – if the worker process is lost (OOM kill,
  segfault), the task is rejected and requeued instead of acked.
* ``worker_prefetch_multiplier = 1`` – each worker fetches only one message
  at a time, preventing large 500 MB ingestion tasks from starving.
* ``visibility_timeout = 7200`` – tasks have up to 2 hours before the broker
  considers them lost and redelivers (suitable for long-running file ingest).

Beat schedule: the stalled-job reaper fires every 3 minutes to detect and
re-dispatch INDEXING documents that have not updated their checkpoint for
more than ``REAPER_STALL_MINUTES`` (default 15).
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "rag_knowledge_assurance",
    broker=_settings.CELERY_BROKER_URL,
    backend=_settings.CELERY_RESULT_BACKEND,
)

# ---------------------------------------------------------------------------
# Production reliability settings
# ---------------------------------------------------------------------------
celery_app.conf.update(
    # Acknowledge only after success – crash → redeliver
    task_acks_late=True,
    # Reject (re-queue) if the worker process is killed
    task_reject_on_worker_lost=True,
    # Fetch one task at a time per worker process
    worker_prefetch_multiplier=1,
    # Give long ingestion tasks up to 2 h before broker redispatches
    broker_transport_options={"visibility_timeout": 7200},
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Route tasks to their queues
    task_routes={
        "app.workers.ingest_tasks.ingest_document_task": {"queue": "ingestion"},
        "app.workers.reaper_tasks.reap_stalled_documents": {"queue": "default"},
    },
    # Default queue for un-routed tasks
    task_default_queue="default",
    # These task modules do not follow Celery's package.tasks convention.
    imports=("app.workers.ingest_tasks", "app.workers.reaper_tasks"),
)

# ---------------------------------------------------------------------------
# Beat schedule
# ---------------------------------------------------------------------------
celery_app.conf.beat_schedule = {
    "reap-stalled-documents-every-3m": {
        "task": "app.workers.reaper_tasks.reap_stalled_documents",
        "schedule": 180.0,  # every 3 minutes
        "options": {"queue": "default"},
    },
}
