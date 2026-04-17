"""HTTP routes for the in-app notification feed (unread count + mark read)."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from src.modules.notifications.models.db_models import Notification
from src.modules.notifications.models.request_models import MarkReadRequest
from src.modules.notifications.models.response_models import (
    MarkReadResponse,
    UnreadCountResponse,
)
from src.shared.database import get_db
from src.shared.middleware.auth import get_current_user
from src.shared.responses import success_response
from src.shared.utils.datetime_utils import get_utc_now

logger = structlog.get_logger()
router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get(
    "/unread-count",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def unread_count(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the caller's unread notification count."""
    count = await db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == str(current_user["user_id"]),
            Notification.read_at.is_(None),
        )
    )
    return success_response(UnreadCountResponse(unread=int(count or 0)))


@router.post(
    "/mark-read",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def mark_read(
    data: MarkReadRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Mark notifications as read.

    When `notification_ids` is empty, every unread row for the caller is
    marked read. When non-empty, only the listed IDs are updated — and only
    those actually owned by the caller. Cross-user IDs are ignored
    silently so this cannot be used to probe for existence.
    """
    now = get_utc_now()
    stmt = (
        update(Notification)
        .where(
            Notification.user_id == str(current_user["user_id"]),
            Notification.read_at.is_(None),
        )
        .values(read_at=now)
    )
    if data.notification_ids:
        stmt = stmt.where(Notification.id.in_(data.notification_ids))

    result = await db.execute(stmt)
    await db.flush()
    return success_response(MarkReadResponse(marked=int(result.rowcount or 0)))
