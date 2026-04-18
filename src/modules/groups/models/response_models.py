"""Pydantic response models for the groups module."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GroupResponse(BaseModel):
    """Single group record."""

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
