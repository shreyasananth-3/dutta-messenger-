"""SQLAlchemy ORM models for the chat module.

Mirrors the shipped baseline schema (`migrations/001_init_schema.sql`
§CHAT). Four tables: conversations, messages, message_reads,
conversation_members. Tenant isolation is transitive through
`conversations.group_id → groups.institution_id`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from src.shared.database import Base, BaseModel


class Conversation(BaseModel):
    """Thread that holds messages.

    Every group has one conversation in simple mode (auto-created by
    the chat service on first send); topics mode has one conversation
    per topic. Both are tagged with `group_id`, optionally `topic_id`.
    """

    __tablename__ = "conversations"

    group_id = Column(
        UUID(as_uuid=False),
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topic_id = Column(
        UUID(as_uuid=False),
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )


class Message(BaseModel):
    """Single message, soft-deleted via `deleted_at`."""

    __tablename__ = "messages"

    conversation_id = Column(
        UUID(as_uuid=False),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content = Column(Text, nullable=False)
    reply_to_message_id = Column(
        UUID(as_uuid=False),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class MessageRead(Base):
    """Per-message read receipt. No updated_at on baseline schema."""

    __tablename__ = "message_reads"

    id = Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )
    message_id = Column(
        UUID(as_uuid=False),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    read_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_message_read"),)


class ConversationMember(Base):
    """Membership in a conversation. Separate from group_members because
    topic conversations have subset memberships (future)."""

    __tablename__ = "conversation_members"

    id = Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )
    conversation_id = Column(
        UUID(as_uuid=False),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    joined_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    left_at = Column(DateTime(timezone=True), nullable=True)
    muted = Column(Boolean, default=False, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("conversation_id", "user_id", name="uq_conv_member"),)
