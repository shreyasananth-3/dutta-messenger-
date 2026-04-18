"""Celery task that talks to FCM for a single notification batch.

Input: a `notification_batches.id` plus the owning `institution_id`. The
task loads the batch, resolves the recipient's active FCM tokens, sends a
multicast push via FCM (or the mock client in CI/dev), and records the
outcome.

The task owns its own short-lived DB session because Celery workers run
outside the FastAPI request lifecycle and cannot reuse the `get_db`
dependency's session. It wraps service calls in an explicit transaction
so audit rows and status updates commit atomically with the outcome.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import UTC
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.modules.notifications.models.db_models import (
    Notification,
    NotificationBatch,
)
from src.modules.notifications.services.fanout_service import FanoutService
from src.modules.notifications.services.token_service import TokenService
from src.shared.celery_app import celery_app
from src.shared.database import SessionLocal
from src.shared.observability.metrics import (
    CELERY_TASK_LATENCY,
    NOTIFICATIONS_DELIVERED,
)

logger = structlog.get_logger()

_TASK_NAME = "notifications.send_push_batch"


@dataclass(frozen=True)
class FcmResponse:
    """Outcome of one FCM multicast send.

    Attributes:
        success_count: Tokens that FCM acknowledged as delivered.
        failure_count: Tokens that FCM rejected.
        unregistered_tokens: Token strings FCM returned `UNREGISTERED` for;
            they are deactivated locally.
        error: Transport-level error (network, auth) if any; a
            non-empty string means the whole batch is a failure.
    """

    success_count: int
    failure_count: int
    unregistered_tokens: list[str]
    error: str | None = None


class FcmClient:
    """Pluggable FCM client. The default uses a deterministic mock.

    Production code substitutes this via `configure_fcm_client(...)`
    during app startup so the Celery worker talks to the real Firebase
    SDK. CI and dev keep the mock client so tests are hermetic.
    """

    def send_multicast(
        self,
        *,
        tokens: list[str],
        title: str,
        body: str,
        data: dict[str, Any] | None,
    ) -> FcmResponse:
        """Send a multicast push. The default impl pretends all succeeded.

        Tests substitute a `MockFcmClient` that captures calls. Production
        swaps in `FirebaseAdminClient` via `configure_fcm_client` at import
        time when `FCM_MOCK_MODE` is false.
        """
        _ = (title, body, data)
        return FcmResponse(
            success_count=len(tokens),
            failure_count=0,
            unregistered_tokens=[],
        )


_client: FcmClient = FcmClient()


def configure_fcm_client(client: FcmClient) -> None:
    """Replace the module-global FCM client. Tests call this directly."""
    global _client
    _client = client


def current_fcm_client() -> FcmClient:
    """Accessor for the active client — kept as a function for test patching."""
    return _client


@celery_app.task(name=_TASK_NAME, bind=True, max_retries=3)
def send_push_batch(self, batch_id: str, institution_id: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]  # pragma: no cover - Celery entry point
    """Send one notification batch via FCM.

    Synchronous Celery entry point. Delegates to an async runner so the
    service layer remains async-native. Not covered by unit tests —
    `asyncio.run(...)` cannot nest inside pytest-asyncio's running loop.
    Exercised by the Celery integration check in Stage 6.
    """
    start = time.monotonic()
    try:
        return asyncio.run(_run_batch(batch_id, institution_id))
    finally:
        CELERY_TASK_LATENCY.labels(task_name=_TASK_NAME).observe(time.monotonic() - start)


async def _run_batch(
    batch_id: str, institution_id: str
) -> dict[str, Any]:  # pragma: no cover - driven by Celery worker only
    """Load the batch, dispatch, and record the outcome.

    Opens a short-lived SessionLocal session because Celery workers run
    outside the FastAPI request lifecycle. Tests drive `run_batch` below
    with their own session so they never touch the production engine.
    """
    inst_uuid = uuid.UUID(institution_id)
    async with SessionLocal() as session:
        try:
            result = await _process_batch(session, batch_id, inst_uuid)
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise


async def run_batch(
    db: AsyncSession,
    *,
    batch_id: str,
    institution_id: uuid.UUID,
) -> dict[str, Any]:
    """Async entry point for tests and in-process callers.

    Skips Celery and the short-lived SessionLocal — uses the caller-supplied
    session so integration tests run inside the rolled-back per-test
    transaction that `tests/conftest.py` provides.
    """
    return await _process_batch(db, batch_id, institution_id)


async def _process_batch(
    db: AsyncSession,
    batch_id: str,
    institution_id: uuid.UUID,
) -> dict[str, Any]:
    batch = await db.scalar(select(NotificationBatch).where(NotificationBatch.id == batch_id))
    if batch is None:
        logger.warning("push_batch_missing", batch_id=batch_id)
        return {"status": "missing"}

    notification = await db.scalar(
        select(Notification).where(Notification.id == batch.notification_ids[0])
    )
    if notification is None:
        await FanoutService.record_batch_result(
            db,
            batch=batch,
            institution_id=institution_id,
            success=False,
            failure_reason="notification_missing",
        )
        NOTIFICATIONS_DELIVERED.labels(result="failure").inc()
        return {"status": "failed", "reason": "notification_missing"}

    tokens = await TokenService.list_active_tokens(db, user_id=uuid.UUID(batch.user_id))
    if not tokens:
        await FanoutService.record_batch_result(
            db,
            batch=batch,
            institution_id=institution_id,
            success=False,
            failure_reason="no_active_tokens",
        )
        NOTIFICATIONS_DELIVERED.labels(result="failure").inc()
        return {"status": "failed", "reason": "no_active_tokens"}

    response = current_fcm_client().send_multicast(
        tokens=[t.token for t in tokens],
        title=notification.title,
        body=notification.body,
        data=_jsonable(notification.data),
    )

    for stale in response.unregistered_tokens:
        await TokenService.deactivate_by_string(db, institution_id=institution_id, token=stale)

    if response.error is not None:
        await FanoutService.record_batch_result(
            db,
            batch=batch,
            institution_id=institution_id,
            success=False,
            failure_reason=response.error,
        )
        NOTIFICATIONS_DELIVERED.labels(result="failure").inc()
        return {
            "status": "failed",
            "reason": response.error,
            "failure_count": len(tokens),
        }

    now_ts = _batch_mark_sent(batch)
    await FanoutService.record_batch_result(
        db,
        batch=batch,
        institution_id=institution_id,
        success=True,
    )
    NOTIFICATIONS_DELIVERED.labels(result="success").inc()
    logger.info(
        "push_batch_sent",
        batch_id=batch_id,
        success_count=response.success_count,
        failure_count=response.failure_count,
    )
    return {
        "status": "sent",
        "success_count": response.success_count,
        "failure_count": response.failure_count,
        "sent_at": now_ts.isoformat(),
    }


def _batch_mark_sent(batch: NotificationBatch) -> Any:
    """Stamp `sent_at` on the batch. Isolated so tests can freeze time."""
    from datetime import datetime

    batch.sent_at = datetime.now(UTC)
    return batch.sent_at


def _jsonable(data: Any) -> dict[str, Any] | None:
    """Coerce SQLAlchemy JSONB column into a plain dict for FCM payload."""
    if data is None:
        return None
    if isinstance(data, dict):
        return data
    return dict(data)


# Production wiring: if FCM_MOCK_MODE is false, swap in the real client.
# The real client is imported lazily so CI/dev do not pay the
# firebase-admin import cost when running tests.
if not settings.FCM_MOCK_MODE:  # pragma: no cover - production wiring
    from src.modules.notifications.tasks._firebase_client import (
        FirebaseAdminClient,
    )

    configure_fcm_client(FirebaseAdminClient())
