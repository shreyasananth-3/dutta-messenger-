"""SQLAlchemy ORM models for the notifications module.

Column names, types, and nullability mirror the shipped schema in
`migrations/001_init_schema.sql` (tables `fcm_tokens`, `notifications`,
`notification_batches`). The `notifications` table has no `updated_at`
column, so that model extends `Base` directly rather than inheriting
`BaseModel` (which mandates `updated_at`).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.sql import func

from src.shared.database import Base, BaseModel


class FcmToken(BaseModel):
    """Registered FCM device token for push delivery.

    One row per (user, device) — the `token` column is globally UNIQUE so a
    token re-registered by another user rebinds to the new owner.
    """

    __tablename__ = "fcm_tokens"

    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token = Column(String(500), nullable=False, unique=True)
    device_name = Column(String(255), nullable=True)
    device_type = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class Notification(Base):
    """In-app notification row for the notification feed.

    Not a `BaseModel` subclass — the shipped schema has no `updated_at`
    column on this table. Reads preserve ordering via `created_at DESC`.
    """

    __tablename__ = "notifications"

    id = Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type = Column(String(100), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    data = Column(JSONB, nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class NotificationBatch(BaseModel):
    """Fan-out batch tracking a Celery push dispatch.

    `notification_ids` is a Postgres UUID[] of `notifications.id` values; the
    batch groups repeat-recipient notifications into one FCM send. `status`
    is one of {"pending", "sent", "failed", "partial"} — see MODULE.md.
    """

    __tablename__ = "notification_batches"

    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notification_ids = Column(ARRAY(UUID(as_uuid=False)), nullable=False)
    status = Column(String(50), default="pending", nullable=True)
    failure_reason = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
