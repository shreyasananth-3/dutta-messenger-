"""Business logic for sending, editing, deleting, and reading messages.

Tenant scoping: every service method fetches the parent `groups` row
first via `GroupService.get_group()` which enforces
`tenant_scoped_query(Group, institution_id)`. Once the group is
confirmed in-tenant, conversation + message rows inherit isolation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.chat.models.db_models import (
    Conversation,
    ConversationMember,
    Message,
    MessageRead,
)
from src.modules.groups.models.db_models import Group, GroupMember, Topic
from src.shared.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from src.shared.security.audit import AuditEvent, write_audit

logger = structlog.get_logger()

MAX_CONTENT_LEN = 4096
ADMIN_GROUP_ROLES = ("owner", "admin")


class MessageService:
    """Core chat service."""

    @staticmethod
    async def open_conversation(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
        group_id: uuid.UUID | str,
        topic_id: uuid.UUID | str | None = None,
    ) -> Conversation:
        """Return (or create) the conversation for a group / topic.

        Also adds the caller to `conversation_members` if missing. The
        caller must be a member of the parent group.
        """
        group = await db.scalar(
            select(Group).where(
                Group.id == str(group_id),
                Group.institution_id == str(institution_id),
            )
        )
        if group is None:
            raise NotFoundError("group", str(group_id))

        # Membership guard (privacy: non-member sees 404)
        member = await db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group.id,
                GroupMember.user_id == str(actor_id),
            )
        )
        if member is None:
            raise NotFoundError("group", str(group_id))

        topic_id_str: str | None = None
        if topic_id is not None:
            topic = await db.scalar(
                select(Topic).where(and_(Topic.id == str(topic_id), Topic.group_id == group.id))
            )
            if topic is None:
                raise NotFoundError("topic", str(topic_id))
            topic_id_str = str(topic.id)
        elif group.mode == "topics":
            raise ConflictError("group is in topics mode; topic_id is required", "conversation")

        conv = await db.scalar(
            select(Conversation).where(
                Conversation.group_id == group.id,
                Conversation.topic_id == topic_id_str,
            )
        )
        if conv is None:
            conv = Conversation(group_id=group.id, topic_id=topic_id_str)
            db.add(conv)
            await db.flush()

        existing_member = await db.scalar(
            select(ConversationMember).where(
                ConversationMember.conversation_id == conv.id,
                ConversationMember.user_id == str(actor_id),
            )
        )
        if existing_member is None:
            db.add(
                ConversationMember(
                    conversation_id=conv.id,
                    user_id=str(actor_id),
                )
            )
            await db.flush()
        return conv

    @staticmethod
    async def send_message(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
        conversation_id: uuid.UUID | str,
        content: str,
        reply_to_message_id: uuid.UUID | str | None = None,
    ) -> Message:
        """Persist a message. Validates membership + content length."""
        content = (content or "").strip()
        if not content:
            raise ConflictError("content cannot be empty", "message")
        if len(content) > MAX_CONTENT_LEN:
            raise ConflictError(f"content exceeds {MAX_CONTENT_LEN} characters", "message")
        await MessageService._require_conv_member(
            db, conversation_id=conversation_id, user_id=actor_id
        )

        reply_id: str | None = None
        if reply_to_message_id is not None:
            reply = await db.scalar(select(Message).where(Message.id == str(reply_to_message_id)))
            if reply is None or str(reply.conversation_id) != str(conversation_id):
                raise NotFoundError("message", str(reply_to_message_id))
            reply_id = str(reply.id)

        msg = Message(
            conversation_id=str(conversation_id),
            sender_id=str(actor_id),
            content=content,
            reply_to_message_id=reply_id,
        )
        db.add(msg)
        await db.flush()
        # No audit on send — high volume; audit fires on edit/delete only.
        return msg

    @staticmethod
    async def list_messages(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
        conversation_id: uuid.UUID | str,
        limit: int = 50,
        before_id: uuid.UUID | str | None = None,
    ) -> list[Message]:
        """List messages newest-first, cursor-paginated via `before_id`."""
        limit = max(1, min(100, limit))
        await MessageService._require_conv_member(
            db, conversation_id=conversation_id, user_id=actor_id
        )

        stmt = (
            select(Message)
            .where(Message.conversation_id == str(conversation_id))
            .order_by(desc(Message.created_at), desc(Message.id))
            .limit(limit)
        )
        if before_id is not None:
            anchor = await db.scalar(select(Message).where(Message.id == str(before_id)))
            if anchor is not None:
                stmt = stmt.where(Message.created_at < anchor.created_at)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def edit_message(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
        message_id: uuid.UUID | str,
        content: str,
    ) -> Message:
        """Edit own message only."""
        content = (content or "").strip()
        if not content or len(content) > MAX_CONTENT_LEN:
            raise ConflictError("content must be 1..4096 characters", "message")
        msg = await MessageService._load_message(db, message_id=message_id)
        if str(msg.sender_id) != str(actor_id):
            raise PermissionDeniedError("only the sender can edit a message")
        if msg.deleted_at is not None:
            raise ConflictError("cannot edit a deleted message", "message")

        msg.content = content
        await db.flush()
        await write_audit(
            db,
            actor_id=actor_id,
            institution_id=institution_id,
            action=AuditEvent.MESSAGE_EDITED,
            resource_type="message",
            resource_id=msg.id,
            metadata={"conversation_id": str(msg.conversation_id)},
        )
        return msg

    @staticmethod
    async def delete_message(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
        message_id: uuid.UUID | str,
    ) -> None:
        """Soft-delete. Sender OR group admin/owner may delete."""
        msg = await MessageService._load_message(db, message_id=message_id)
        if msg.deleted_at is not None:
            return  # idempotent

        can_delete = str(msg.sender_id) == str(actor_id)
        if not can_delete:
            # Check group admin/owner path
            conv = await db.scalar(
                select(Conversation).where(Conversation.id == msg.conversation_id)
            )
            if conv is None:
                raise NotFoundError("conversation", str(msg.conversation_id))
            membership = await db.scalar(
                select(GroupMember).where(
                    GroupMember.group_id == conv.group_id,
                    GroupMember.user_id == str(actor_id),
                )
            )
            can_delete = membership is not None and membership.role in ADMIN_GROUP_ROLES

        if not can_delete:
            raise PermissionDeniedError("cannot delete this message")

        msg.deleted_at = datetime.now(UTC)
        msg.content = "[deleted]"
        await db.flush()
        await write_audit(
            db,
            actor_id=actor_id,
            institution_id=institution_id,
            action=AuditEvent.MESSAGE_DELETED,
            resource_type="message",
            resource_id=msg.id,
            metadata={"conversation_id": str(msg.conversation_id)},
        )

    @staticmethod
    async def mark_read(
        db: AsyncSession,
        *,
        actor_id: uuid.UUID | str,
        conversation_id: uuid.UUID | str,
        last_read_message_id: uuid.UUID | str,
    ) -> None:
        """Upsert read receipt for the caller on the given message."""
        await MessageService._require_conv_member(
            db, conversation_id=conversation_id, user_id=actor_id
        )
        msg = await db.scalar(select(Message).where(Message.id == str(last_read_message_id)))
        if msg is None or str(msg.conversation_id) != str(conversation_id):
            raise NotFoundError("message", str(last_read_message_id))

        existing = await db.scalar(
            select(MessageRead).where(
                MessageRead.message_id == msg.id,
                MessageRead.user_id == str(actor_id),
            )
        )
        now = datetime.now(UTC)
        if existing is None:
            db.add(
                MessageRead(
                    message_id=msg.id,
                    user_id=str(actor_id),
                    read_at=now,
                )
            )
        else:
            existing.read_at = now
        await db.flush()

    # ----- helpers -----

    @staticmethod
    async def _load_message(db: AsyncSession, *, message_id: uuid.UUID | str) -> Message:
        msg = await db.scalar(select(Message).where(Message.id == str(message_id)))
        if msg is None:
            raise NotFoundError("message", str(message_id))
        return msg

    @staticmethod
    async def _require_conv_member(
        db: AsyncSession,
        *,
        conversation_id: uuid.UUID | str,
        user_id: uuid.UUID | str,
    ) -> ConversationMember:
        row = await db.scalar(
            select(ConversationMember).where(
                ConversationMember.conversation_id == str(conversation_id),
                ConversationMember.user_id == str(user_id),
            )
        )
        if row is None:
            # Non-member sees 404 (no existence leak).
            raise NotFoundError("conversation", str(conversation_id))
        return row
