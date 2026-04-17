"""Celery app instance for the notifications module.

Kept inside the notifications module until a shared `src/shared/celery_app.py`
is introduced — this is the only module with Celery tasks today and
pulling the instance into `src/shared/` should be a coordinated change
once another module (erasure, analytics) needs it.
"""

from __future__ import annotations

from celery import Celery

from src.config import settings

celery_app = Celery(
    "dutta_messenger_notifications",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_queue="notifications",
    broker_connection_retry_on_startup=True,
)

# Task modules are registered by importing them at app boot.
celery_app.autodiscover_tasks(["src.modules.notifications.tasks"])
