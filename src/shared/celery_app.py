"""Celery application instance for DuttaMessenger background tasks.

All modules that enqueue tasks import this single `celery_app` and register
their tasks against it. Modules known to consume this in Stage 4:

- `notifications` — FCM push fanout per conversation
- `media` — thumbnail generation on upload complete
- `privacy-erasure` (cross-module) — async right-to-erasure + SAR bundle export

See `docs/design/privacy-erasure.md` (SAR / erasure SLAs) and
`reference-docs/modules/notifications/MODULE.md` for the task contracts that
will land in Stage 4. No tasks are registered here — the container does
nothing until a module imports this and defines its own `@celery_app.task`.

Broker and result backend come from `src/config.py`:
    CELERY_BROKER_URL     → redis://localhost:6379/1 (dev default)
    CELERY_RESULT_BACKEND → redis://localhost:6379/2 (dev default)
"""

from __future__ import annotations

from celery import Celery

from src.config import settings

celery_app = Celery(
    "dutta_messenger",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    # Modules add their task modules here by calling
    # `celery_app.autodiscover_tasks(["src.modules.notifications", ...])`
    # from `src/main.py` when their ENABLE_* flag is on. For now no tasks
    # are registered.
    include=[],
)

celery_app.conf.update(
    # Serialisation — JSON only; no pickle (security).
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Time zone — UTC everywhere in the app (see src/shared/utils/datetime_utils.py).
    timezone="UTC",
    enable_utc=True,
    # Reliability — acknowledge only after the task succeeds so crashes
    # during execution cause re-delivery rather than silent drops.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Observability — emit per-task state transitions to Flower / logs.
    task_track_started=True,
    # Safety — bound task runtimes so a hung task doesn't block a worker.
    # Individual tasks may override via `@celery_app.task(time_limit=...)`.
    task_time_limit=300,  # hard kill at 5 min
    task_soft_time_limit=240,  # soft warning at 4 min
    # Memory hygiene — recycle workers to release RAM held by long-running
    # processes (e.g. PIL image objects during thumbnail generation).
    worker_max_tasks_per_child=1000,
    # Result TTL — keep task results for 1 hour; longer values bloat Redis.
    result_expires=3600,
)


__all__ = ["celery_app"]
