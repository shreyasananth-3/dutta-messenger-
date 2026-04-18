"""Pydantic response models for the chat module."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    group_id: uuid.UUID
    topic_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    sender_id: uuid.UUID
    content: str | None
    reply_to_message_id: uuid.UUID | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MarkReadResponse(BaseModel):
    conversation_id: uuid.UUID
    last_read_message_id: uuid.UUID
