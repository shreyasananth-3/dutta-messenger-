"""SQLAlchemy ORM models for the groups module.

Mirrors the baseline schema in `migrations/001_init_schema.sql` §GROUPS.
Reference-docs SCHEMA.sql drifts in places (topics.access_mode,
topics.sort_order, groups.conversation_id FK); we follow the baseline
since that's what's actually in Postgres. Advanced topic access-mode
enforcement is deferred to a follow-up migration.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from src.shared.database import Base, BaseModel


class Group(BaseModel):
    """Group — a named container for members and conversations.

    `mode` is either `simple` (one shared conversation) or `topics`
    (many conversations, one per topic). See MODULE.md for semantics.
    """

    __tablename__ = "groups"

    institution_id = Column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    avatar_url = Column(Text, nullable=True)
    mode = Column(String(50), nullable=False, default="simple")
    created_by_user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_archived = Column(Boolean, default=False, nullable=True)
    pinned_message_id = Column(UUID(as_uuid=False), nullable=True)


class Topic(BaseModel):
    """Topic within a topic-enabled group."""

    __tablename__ = "topics"

    group_id = Column(
        UUID(as_uuid=False),
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    icon_emoji = Column(String(10), nullable=True)
    created_by_user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("group_id", "name", name="uq_topic_group_name"),)


class GroupMember(Base):
    """Membership row linking a user to a group with a role.

    Not a `BaseModel` subclass — the shipped schema does not add
    `updated_at` on `group_members` (membership is append-only;
    role changes happen via separate audit-tracked endpoints).
    """

    __tablename__ = "group_members"

    id = Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )
    group_id = Column(
        UUID(as_uuid=False),
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(50), nullable=False, default="member")
    joined_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)
