"""Pydantic response models for the notifications module."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class FcmTokenResponse(BaseModel):
    """One registered FCM token — sensitive `token` string omitted."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    device_name: str | None
    device_type: str | None
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RegisterTokenResponse(BaseModel):
    """Response envelope for token registration.

    `reused` is `True` when the caller registered a token they already owned
    (same `(user_id, token)` combination) — the server returned 200 instead
    of creating a duplicate row, per the idempotency RFC.
    """

    token: FcmTokenResponse
    reused: bool


class UnreadCountResponse(BaseModel):
    """Response envelope for the unread-count endpoint."""

    unread: int


class MarkReadResponse(BaseModel):
    """Response envelope for the mark-read endpoint."""

    marked: int


class NotificationResponse(BaseModel):
    """One row from the notification feed."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    type: str
    title: str
    body: str
    data: dict[str, Any] | None
    read_at: datetime | None
    created_at: datetime
