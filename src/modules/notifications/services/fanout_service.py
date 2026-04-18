"""Fan-out service — batches notifications for push dispatch.

Given a payload (a new message, a mention, etc.) and a list of recipient
user IDs, the fanout service:

1. Persists a `notifications` row per recipient so the in-app feed has a
   record regardless of push outcome.
2. Groups the freshly-inserted notification IDs into `notification_batches`
   rows sized to `FCM_BATCH_MAX_RECIPIENTS`.
3. Enqueues the Celery push task for each batch. The chat module calls
   `FanoutService.dispatch_message_notifications(...)`; the task itself
   (`src/modules/notifications/tasks/push_task.py`) reads the batch row
   and talks to FCM.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.modules.notifications.models.db_models import (
    Notification,
    NotificationBatch,
)
from src.shared.security.audit import AuditEvent, write_audit

logger = structlog.get_logger()


class FanoutService:
    """Service methods for notification fanout."""

    @staticmethod
    async def dispatch_message_notifications(
        db: AsyncSession,
        *,
        sender_id: uuid.UUID,
        institution_id: uuid.UUID,
        recipient_user_ids: list[uuid.UUID],
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> list[NotificationBatch]:
        """Persist notifications + create batch rows + enqueue push tasks.

        Args:
            db: Async database session (transaction managed by the caller).
            sender_id: The user whose action triggered this fanout. Used as
                `actor_id` on the audit row; recipients may equal the
                sender (broadcast scenarios).
            institution_id: Institution scope for the audit row.
            recipient_user_ids: Users that should receive the push. The
                chat module pre-filters offline recipients.
            title: FCM notification title.
            body: FCM notification body.
            data: Optional structured payload (conversation_id, message_id,
                etc.). Stored verbatim on each `notifications` row.

        Returns:
            The persisted `NotificationBatch` rows, in enqueue order.
        """
        if not recipient_user_ids:
            return []

        payload = dict(data or {})
        persisted_ids_by_user: dict[uuid.UUID, str] = {}
        for recipient_id in recipient_user_ids:
            note = Notification(
                user_id=str(recipient_id),
                type=payload.get("type", "message"),
                title=title,
                body=body,
                data=payload,
            )
            db.add(note)
            await db.flush()
            persisted_ids_by_user[recipient_id] = note.id

        batches: list[NotificationBatch] = []
        cap = max(1, settings.FCM_BATCH_MAX_RECIPIENTS)
        recipients = list(persisted_ids_by_user.items())
        for chunk_start in range(0, len(recipients), cap):
            chunk = recipients[chunk_start : chunk_start + cap]
            for recipient_id, note_id in chunk:
                batch = NotificationBatch(
                    user_id=str(recipient_id),
                    notification_ids=[note_id],
                    status="pending",
                )
                db.add(batch)
                await db.flush()
                batches.append(batch)

        await write_audit(
            db,
            actor_id=sender_id,
            institution_id=institution_id,
            action=AuditEvent.NOTIFICATION_BATCH_SENT,
            resource_type="notification_batch",
            resource_id=None,
            metadata={
                "batch_count": len(batches),
                "recipient_count": len(recipient_user_ids),
            },
        )

        for batch in batches:
            _enqueue_batch(batch.id, institution_id)

        logger.info(
            "notification_batches_enqueued",
            batch_count=len(batches),
            recipient_count=len(recipient_user_ids),
        )
        return batches

    @staticmethod
    async def record_batch_result(
        db: AsyncSession,
        *,
        batch: NotificationBatch,
        institution_id: uuid.UUID,
        success: bool,
        failure_reason: str | None = None,
    ) -> None:
        """Mark a batch as sent/failed and emit an audit row.

        Called by the Celery push task. Either `success=True` (sets
        `status="sent"` and writes a `notification.batch.sent` audit) or
        `success=False` (sets `status="failed"`, stores
        `failure_reason`, writes `notification.batch.failed`).
        """
        if success:
            batch.status = "sent"
            batch.failure_reason = None
            action = AuditEvent.NOTIFICATION_BATCH_SENT
            metadata: dict[str, Any] = {"batch_id": str(batch.id)}
        else:
            batch.status = "failed"
            batch.failure_reason = failure_reason or "unknown"
            action = AuditEvent.NOTIFICATION_BATCH_FAILED
            metadata = {
                "batch_id": str(batch.id),
                "failure_reason": batch.failure_reason,
            }

        await db.flush()
        await write_audit(
            db,
            actor_id=batch.user_id,
            institution_id=institution_id,
            action=action,
            resource_type="notification_batch",
            resource_id=batch.id,
            metadata=metadata,
        )


def _enqueue_batch(batch_id: str, institution_id: uuid.UUID) -> None:
    """Send the batch onto the Celery queue.

    Isolated so tests can monkey-patch it without pulling Celery's broker
    into the test harness. In production Celery runs with a Redis broker.
    """
    from src.modules.notifications.tasks.push_task import send_push_batch

    send_push_batch.delay(str(batch_id), str(institution_id))
