"""Pydantic response models for the groups module."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GroupResponse(BaseModel):
    """Single group record.

    `member_count` is computed per-request rather than stored on the row
    (rows would drift after every add/remove). The list endpoint uses
    `count_members_bulk` to avoid N+1.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    institution_id: uuid.UUID
    name: str
    description: str | None
    avatar_url: str | None
    mode: str
    created_by_user_id: uuid.UUID
    is_archived: bool | None
    created_at: datetime
    updated_at: datetime
    member_count: int = 0


class GroupMemberResponse(BaseModel):
    """Membership row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    group_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    joined_at: datetime


class TopicResponse(BaseModel):
    """Topic within a topics-mode group."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    group_id: uuid.UUID
    name: str
    description: str | None
    icon_emoji: str | None
    created_by_user_id: uuid.UUID
    created_at: datetime


class AddMemberResponse(BaseModel):
    """Outcome of adding a member — idempotent."""

    group_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    reused: bool


class TopicDeleteResponse(BaseModel):
    """Result of deleting a topic — returns the updated group + the
    remaining topic list so the client can hard-replace local state
    without a follow-up GET (audit 3.1, option a)."""

    deleted: bool
    group: GroupResponse
    topics: list[TopicResponse]
