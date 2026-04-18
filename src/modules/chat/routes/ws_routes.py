"""WebSocket endpoint for real-time chat.

Minimal implementation per reference-docs/modules/chat/WEBSOCKET.md:
- First client frame must be `{"type": "auth", "token": "..."}`.
- Server responds with `connection.established`.
- Client sends `message.send`; server persists + broadcasts to all
  connected members of the conversation.

Advanced features (backpressure queue, ping/pong, replay on reconnect,
token.expiring, presence) are deferred — see MODULE.md § Deferred.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from sqlalchemy import select

from src.config import settings
from src.modules.chat.models.db_models import ConversationMember
from src.modules.chat.services.message_service import MessageService
from src.shared.database import SessionLocal


def _decode_token(token: str) -> dict[str, Any] | None:
    """Best-effort JWT decode — returns None on invalid/expired."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


logger = structlog.get_logger()
router = APIRouter()


# In-process connection registry — one entry per active WS.
# For multi-worker deployments this gets replaced with a Redis pub/sub
# fanout (see docs/design/websocket-scaling.md); inline fanout is
# correct for a single-worker dev/test target.
_connections: dict[str, list[WebSocket]] = defaultdict(list)


async def _broadcast(conversation_id: str, frame: dict[str, Any]) -> None:
    """Send `frame` to every connection open on `conversation_id`."""
    dead: list[WebSocket] = []
    for ws in list(_connections.get(conversation_id, [])):
        try:
            await ws.send_json(frame)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _connections[conversation_id].remove(ws)


@router.websocket("/ws/chat")
async def chat_ws(ws: WebSocket) -> None:  # pragma: no cover - exercised via manual smoke + Stage 6
    """Real-time chat socket. One connection per caller.

    Protocol:
      1. client → server: {"type": "auth", "token": "<jwt>"}
      2. server → client: {"type": "connection.established", "user_id": "..."}
      3. client subscribes to a conversation with
         {"type": "subscribe", "conversation_id": "..."}
      4. client sends
         {"type": "message.send", "conversation_id": "...", "content": "...", "reply_to_message_id"?: "..."}
         server persists, emits {"type": "message.new", "message": {...}} to all
         subscribers of that conversation.
    """
    await ws.accept()
    subscribed: set[str] = set()
    user_id: str | None = None
    institution_id: str | None = None

    try:
        first = await ws.receive_text()
        try:
            msg = json.loads(first)
        except json.JSONDecodeError:
            await ws.close(code=4001)
            return
        if msg.get("type") != "auth" or not msg.get("token"):
            await ws.close(code=4001)
            return
        claims = _decode_token(msg["token"])
        if not claims:
            await ws.close(code=4001)
            return
        user_id = str(claims.get("sub"))
        institution_id = str(claims.get("inst") or "")
        await ws.send_json(
            {
                "type": "connection.established",
                "user_id": user_id,
                "institution_id": institution_id,
            }
        )

        while True:
            raw = await ws.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "invalid_json"})
                continue

            ftype = frame.get("type")
            if ftype == "subscribe":
                conv_id = str(frame.get("conversation_id") or "")
                async with SessionLocal() as session:
                    member = await session.scalar(
                        select(ConversationMember).where(
                            ConversationMember.conversation_id == conv_id,
                            ConversationMember.user_id == user_id,
                        )
                    )
                if member is None:
                    await ws.send_json({"type": "error", "message": "not_a_member"})
                    continue
                _connections[conv_id].append(ws)
                subscribed.add(conv_id)
                await ws.send_json({"type": "subscribed", "conversation_id": conv_id})

            elif ftype == "message.send":
                conv_id = str(frame.get("conversation_id") or "")
                if conv_id not in subscribed:
                    await ws.send_json({"type": "error", "message": "subscribe_first"})
                    continue
                content = str(frame.get("content") or "")
                reply_to = frame.get("reply_to_message_id")
                async with SessionLocal() as session:
                    try:
                        msg_row = await MessageService.send_message(
                            session,
                            institution_id=uuid.UUID(institution_id),
                            actor_id=uuid.UUID(user_id),
                            conversation_id=uuid.UUID(conv_id),
                            content=content,
                            reply_to_message_id=uuid.UUID(reply_to) if reply_to else None,
                        )
                        await session.commit()
                    except Exception as exc:
                        await session.rollback()
                        await ws.send_json({"type": "error", "message": str(exc)[:120]})
                        continue

                payload = {
                    "type": "message.new",
                    "message": {
                        "id": str(msg_row.id),
                        "conversation_id": conv_id,
                        "sender_id": user_id,
                        "content": msg_row.content,
                        "reply_to_message_id": str(msg_row.reply_to_message_id)
                        if msg_row.reply_to_message_id
                        else None,
                        "created_at": msg_row.created_at.isoformat()
                        if msg_row.created_at
                        else None,
                    },
                }
                await _broadcast(conv_id, payload)

            elif ftype == "ping":
                await ws.send_json({"type": "pong"})

            else:
                await ws.send_json({"type": "error", "message": f"unknown frame type: {ftype}"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("ws_chat_unexpected_error", error=str(exc))
    finally:
        for cid in subscribed:
            try:
                _connections[cid].remove(ws)
            except ValueError:
                pass
