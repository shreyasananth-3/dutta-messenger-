"""HTTP routes for the chat module.

Endpoints cover the REST contract per reference-docs/modules/chat/API.md
with light simplifications (no media attachments yet, no pin endpoints
yet — deferred to a follow-up). WebSocket lives in `ws_routes.py`.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.chat.models.request_models import (
    EditMessageRequest,
    MarkReadRequest,
    OpenConversationRequest,
    SendMessageRequest,
)
from src.modules.chat.models.response_models import (
    ConversationResponse,
    MarkReadResponse,
    MessageResponse,
)
from src.modules.chat.routes.ws_routes import _broadcast
from src.modules.chat.services.message_service import MessageService
from src.shared.database import get_db
from src.shared.middleware.auth import get_current_user
from src.shared.responses import success_response

logger = structlog.get_logger()
router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/conversations/open-group",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def open_group_conversation(
    data: OpenConversationRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return (or create) the conversation for a group/topic pair."""
    conv = await MessageService.open_conversation(
        db,
        institution_id=current_user["institution_id"],
        actor_id=current_user["user_id"],
        group_id=data.group_id,
        topic_id=data.topic_id,
    )
    return success_response(ConversationResponse.model_validate(conv))


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=dict[str, Any],
)
async def list_messages(
    conversation_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    before_id: uuid.UUID | None = Query(default=None),
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Cursor-paginated list of messages (newest-first)."""
    rows = await MessageService.list_messages(
        db,
        institution_id=current_user["institution_id"],
        actor_id=current_user["user_id"],
        conversation_id=conversation_id,
        limit=limit,
        before_id=before_id,
    )
    return success_response([MessageResponse.model_validate(m) for m in rows])


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    conversation_id: uuid.UUID,
    data: SendMessageRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Send a message. REST fallback for the WebSocket path."""
    msg = await MessageService.send_message(
        db,
        institution_id=current_user["institution_id"],
        actor_id=current_user["user_id"],
        conversation_id=conversation_id,
        content=data.content,
        reply_to_message_id=data.reply_to_message_id,
    )
    payload = MessageResponse.model_validate(msg)
    await _broadcast(str(conversation_id), {"type": "message.new", "message": payload.model_dump(mode="json")})
    return success_response(payload)


@router.patch(
    "/messages/{message_id}",
    response_model=dict[str, Any],
)
async def edit_message(
    message_id: uuid.UUID,
    data: EditMessageRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Edit own message."""
    msg = await MessageService.edit_message(
        db,
        institution_id=current_user["institution_id"],
        actor_id=current_user["user_id"],
        message_id=message_id,
        content=data.content,
    )
    return success_response(MessageResponse.model_validate(msg))


@router.delete(
    "/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_message(
    message_id: uuid.UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a message. Sender or group admin/owner only."""
    await MessageService.delete_message(
        db,
        institution_id=current_user["institution_id"],
        actor_id=current_user["user_id"],
        message_id=message_id,
    )


@router.post(
    "/conversations/{conversation_id}/read",
    response_model=dict[str, Any],
)
async def mark_read(
    conversation_id: uuid.UUID,
    data: MarkReadRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Upsert the caller's read receipt up to `last_read_message_id`."""
    await MessageService.mark_read(
        db,
        actor_id=current_user["user_id"],
        conversation_id=conversation_id,
        last_read_message_id=data.last_read_message_id,
    )
    return success_response(
        MarkReadResponse(
            conversation_id=conversation_id,
            last_read_message_id=data.last_read_message_id,
        )
    )
