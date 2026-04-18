"""Pydantic request models for the groups module."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class CreateGroupRequest(BaseModel):
    """Body for `POST /api/v1/groups`."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    mode: Literal["simple", "topics"] = "simple"


class UpdateGroupRequest(BaseModel):
    """Body for `PATCH /api/v1/groups/{id}`."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    avatar_url: str | None = Field(default=None, max_length=2000)


class AddMemberRequest(BaseModel):
    """Body for `POST /api/v1/groups/{id}/members`."""

    user_id: uuid.UUID
    role: Literal["member", "admin"] = "member"


class CreateTopicRequest(BaseModel):
    """Body for `POST /api/v1/groups/{id}/topics`."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    icon_emoji: str | None = Field(default=None, max_length=10)
