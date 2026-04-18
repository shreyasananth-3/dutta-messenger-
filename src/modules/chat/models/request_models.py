"""Pydantic request models for the chat module."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class SendMessageRequest(BaseModel):
    """Body for `POST /api/v1/chat/conversations/{id}/messages`."""

    content: str = Field(..., min_length=1, max_length=4096)
    reply_to_message_id: uuid.UUID | None = None


class EditMessageRequest(BaseModel):
    """Body for `PATCH /api/v1/chat/messages/{id}`."""

    content: str = Field(..., min_length=1, max_length=4096)


class MarkReadRequest(BaseModel):
    """Body for `POST /api/v1/chat/conversations/{id}/read`."""

    last_read_message_id: uuid.UUID


class OpenConversationRequest(BaseModel):
    """Body for `POST /api/v1/chat/conversations/open-group` — ensures a
    conversation exists for the given group (simple mode) or topic."""

    group_id: uuid.UUID
    topic_id: uuid.UUID | None = None
