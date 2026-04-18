"""Pydantic response models for the users module."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserProfileResponse(BaseModel):
    """Public profile view of a user — returned by /me and /{id}.

    `email` is included for /me but stripped for /{id} lookups (the route
    layer decides which to return; the model carries the field either way
    so we can reuse one shape).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    institution_id: uuid.UUID
    email: str | None = None
    full_name: str
    avatar_url: str | None = None
    bio: str | None = None
    phone_number: str | None = None
    status: str | None = None
    is_active: bool
    is_online: bool = False
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class UserSearchResultItem(BaseModel):
    """One user in a search result list. Strips email and phone — search
    results are public to the institution but not a PII dump."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    avatar_url: str | None = None
    bio: str | None = None
    status: str | None = None
    is_online: bool = False


class UserSearchResponse(BaseModel):
    """Paginated search response. Cursor-based per CLAUDE.md."""

    results: list[UserSearchResultItem]
    has_more: bool = False
    next_cursor: str | None = None


class OnlineStatusResponse(BaseModel):
    """Map of user_id -> online boolean."""

    online: dict[uuid.UUID, bool]


class UserSettingsResponse(BaseModel):
    """Per-user settings view."""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    notification_messages: bool
    notification_groups: bool
    notification_sound: bool
    theme: str
    language: str
    created_at: datetime
    updated_at: datetime
