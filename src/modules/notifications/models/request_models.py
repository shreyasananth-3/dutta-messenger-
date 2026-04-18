"""Pydantic request models for the notifications module."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterTokenRequest(BaseModel):
    """Client payload for `POST /api/v1/notifications/tokens`."""

    token: str = Field(..., min_length=1, max_length=500)
    device_name: str | None = Field(None, max_length=255)
    device_type: str | None = Field(None, max_length=50, pattern=r"^(ios|android|web)$")


class MarkReadRequest(BaseModel):
    """Client payload for `POST /api/v1/notifications/mark-read`.

    When `notification_ids` is empty, marks every unread notification for
    the caller. An explicit list limits the mutation to those IDs.
    """

    notification_ids: list[str] = Field(default_factory=list, max_length=500)
