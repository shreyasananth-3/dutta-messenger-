"""Business logic for the groups module.

Handles creation, membership, topics, and archive. Audit rows emit for
every mutation inside the same transaction. Tenant isolation enforced
via `tenant_scoped_query(Group, institution_id)` on every read path.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.groups.models.db_models import Group, GroupMember, Topic
from src.shared.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from src.shared.security.audit import AuditEvent, write_audit
from src.shared.security.tenant import tenant_scoped_query

logger = structlog.get_logger()


VALID_MODES = ("simple", "topics")
OWNER_ROLES = ("owner", "admin")


class GroupService:
    """All group / membership / topic business logic."""

    @staticmethod
    async def create_group(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        creator_id: uuid.UUID | str,
        name: str,
        description: str | None = None,
        mode: str = "simple",
    ) -> Group:
        """Create a group with the caller as owner.

        Auto-seeds one `group_members` row (role=owner) and, for
        topics-mode groups, a default "General" topic.
        """
        if mode not in VALID_MODES:
            raise ConflictError(f"Unknown mode '{mode}'", "group")

        group = Group(
            institution_id=str(institution_id),
            name=name,
            description=description,
            mode=mode,
            created_by_user_id=str(creator_id),
        )
        db.add(group)
        await db.flush()

        db.add(
            GroupMember(
                group_id=group.id,
                user_id=str(creator_id),
                role="owner",
            )
        )
        if mode == "topics":
            db.add(
                Topic(
                    group_id=group.id,
                    name="General",
                    description="Default topic",
                    icon_emoji="💬",
                    created_by_user_id=str(creator_id),
                )
            )
        await db.flush()
        await write_audit(
            db,
            actor_id=creator_id,
            institution_id=institution_id,
            action=AuditEvent.GROUP_CREATED,
            resource_type="group",
            resource_id=group.id,
            metadata={"name": name, "mode": mode},
        )
        return group

    @staticmethod
    async def get_group(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        group_id: uuid.UUID | str,
    ) -> Group:
        """Fetch a group, raising NotFoundError on miss / cross-tenant."""
        result = await db.execute(
            tenant_scoped_query(Group, institution_id).where(Group.id == str(group_id))
        )
        group = result.scalar_one_or_none()
        if group is None:
            raise NotFoundError("group", str(group_id))
        return group

    @staticmethod
    async def count_members(
        db: AsyncSession,
        *,
        group_id: uuid.UUID | str,
    ) -> int:
        """Single-group member count. Used by detail endpoints."""
        n = await db.scalar(
            select(func.count())
            .select_from(GroupMember)
            .where(GroupMember.group_id == str(group_id))
        )
        return int(n or 0)

    @staticmethod
    async def count_members_bulk(
        db: AsyncSession,
        *,
        group_ids: list[str],
    ) -> dict[str, int]:
        """Bulk member counts for a list endpoint — one query, not N.

        Returns a {group_id -> count} dict. Group ids with no members
        won't appear in the dict; callers should default to 0.
        """
        if not group_ids:
            return {}
        rows = await db.execute(
            select(GroupMember.group_id, func.count())
            .where(GroupMember.group_id.in_(group_ids))
            .group_by(GroupMember.group_id)
        )
        return {str(gid): int(count) for gid, count in rows.all()}

    @staticmethod
    async def list_user_groups(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        user_id: uuid.UUID | str,
        include_archived: bool = False,
    ) -> list[Group]:
        """Groups the user belongs to, newest-first.

        Joins through `group_members` so non-members don't see the group
        even if they can guess the ID.
        """
        stmt = (
            select(Group)
            .join(GroupMember, GroupMember.group_id == Group.id)
            .where(
                Group.institution_id == str(institution_id),
                GroupMember.user_id == str(user_id),
            )
            .order_by(Group.updated_at.desc())
        )
        if not include_archived:
            stmt = stmt.where(Group.is_archived.is_not(True))
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_group(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        group_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
        name: str | None = None,
        description: str | None = None,
        avatar_url: str | None = None,
    ) -> Group:
        """Patch group metadata — admin/owner only."""
        group = await GroupService.get_group(db, institution_id=institution_id, group_id=group_id)
        await GroupService._require_role(
            db, group_id=group.id, user_id=actor_id, allowed=OWNER_ROLES
        )

        changed: dict[str, str] = {}
        if name is not None:
            group.name = name
            changed["name"] = name
        if description is not None:
            group.description = description
            changed["description"] = description
        if avatar_url is not None:
            group.avatar_url = avatar_url
            changed["avatar_url"] = avatar_url

        await db.flush()
        await write_audit(
            db,
            actor_id=actor_id,
            institution_id=institution_id,
            action=AuditEvent.GROUP_UPDATED,
            resource_type="group",
            resource_id=group.id,
            metadata={"changed": list(changed)},
        )
        return group

    @staticmethod
    async def archive_group(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        group_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
    ) -> None:
        """Soft-delete (archive) a group. Owner/admin only."""
        group = await GroupService.get_group(db, institution_id=institution_id, group_id=group_id)
        await GroupService._require_role(
            db, group_id=group.id, user_id=actor_id, allowed=("owner",)
        )
        group.is_archived = True
        await db.flush()
        await write_audit(
            db,
            actor_id=actor_id,
            institution_id=institution_id,
            action=AuditEvent.GROUP_ARCHIVED,
            resource_type="group",
            resource_id=group.id,
            metadata={},
        )

    @staticmethod
    async def add_member(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        group_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
        target_user_id: uuid.UUID | str,
        role: str = "member",
    ) -> tuple[GroupMember, bool]:
        """Add a user to the group. Idempotent — returns (row, reused)."""
        group = await GroupService.get_group(db, institution_id=institution_id, group_id=group_id)
        await GroupService._require_role(
            db, group_id=group.id, user_id=actor_id, allowed=OWNER_ROLES
        )
        existing = await db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group.id,
                GroupMember.user_id == str(target_user_id),
            )
        )
        if existing is not None:
            return existing, True

        row = GroupMember(
            group_id=group.id,
            user_id=str(target_user_id),
            role=role,
        )
        db.add(row)
        await db.flush()
        await write_audit(
            db,
            actor_id=actor_id,
            institution_id=institution_id,
            action=AuditEvent.GROUP_MEMBER_ADDED,
            resource_type="group",
            resource_id=group.id,
            metadata={"target_user_id": str(target_user_id), "role": role},
        )
        return row, False

    @staticmethod
    async def remove_member(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        group_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
        target_user_id: uuid.UUID | str,
    ) -> None:
        """Remove a user from the group. Admin/owner only; owner self-removal blocked."""
        group = await GroupService.get_group(db, institution_id=institution_id, group_id=group_id)
        await GroupService._require_role(
            db, group_id=group.id, user_id=actor_id, allowed=OWNER_ROLES
        )
        row = await db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group.id,
                GroupMember.user_id == str(target_user_id),
            )
        )
        if row is None:
            raise NotFoundError("group_member", f"{group.id}:{target_user_id}")
        if row.role == "owner":
            raise PermissionDeniedError("group owner cannot be removed")

        await db.delete(row)
        await db.flush()
        await write_audit(
            db,
            actor_id=actor_id,
            institution_id=institution_id,
            action=AuditEvent.GROUP_MEMBER_REMOVED,
            resource_type="group",
            resource_id=group.id,
            metadata={"target_user_id": str(target_user_id)},
        )

    @staticmethod
    async def list_members(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        group_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
    ) -> list[GroupMember]:
        """List members — any group member can read the roster."""
        group = await GroupService.get_group(db, institution_id=institution_id, group_id=group_id)
        await GroupService._require_membership(db, group_id=group.id, user_id=actor_id)
        result = await db.execute(
            select(GroupMember)
            .where(GroupMember.group_id == group.id)
            .order_by(GroupMember.joined_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_topics(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        group_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
    ) -> list[Topic]:
        """Topics in a topics-mode group."""
        group = await GroupService.get_group(db, institution_id=institution_id, group_id=group_id)
        await GroupService._require_membership(db, group_id=group.id, user_id=actor_id)
        if group.mode != "topics":
            return []
        result = await db.execute(
            select(Topic).where(Topic.group_id == group.id).order_by(Topic.created_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def create_topic(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        group_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
        name: str,
        description: str | None = None,
        icon_emoji: str | None = None,
    ) -> Topic:
        """Create a topic. Admin/owner only. Requires `topics` mode."""
        group = await GroupService.get_group(db, institution_id=institution_id, group_id=group_id)
        if group.mode != "topics":
            raise ConflictError("Cannot create topics in a simple-mode group", "topic")
        await GroupService._require_role(
            db, group_id=group.id, user_id=actor_id, allowed=OWNER_ROLES
        )
        existing = await db.scalar(
            select(Topic).where(and_(Topic.group_id == group.id, Topic.name == name))
        )
        if existing is not None:
            raise ConflictError(f"Topic '{name}' already exists", "topic")

        topic = Topic(
            group_id=group.id,
            name=name,
            description=description,
            icon_emoji=icon_emoji,
            created_by_user_id=str(actor_id),
        )
        db.add(topic)
        await db.flush()
        await write_audit(
            db,
            actor_id=actor_id,
            institution_id=institution_id,
            action=AuditEvent.TOPIC_CREATED,
            resource_type="topic",
            resource_id=topic.id,
            metadata={"group_id": str(group.id), "name": name},
        )
        return topic

    @staticmethod
    async def delete_topic(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        group_id: uuid.UUID | str,
        topic_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
    ) -> None:
        """Delete a topic. Admin/owner only."""
        group = await GroupService.get_group(db, institution_id=institution_id, group_id=group_id)
        await GroupService._require_role(
            db, group_id=group.id, user_id=actor_id, allowed=OWNER_ROLES
        )
        topic = await db.scalar(
            select(Topic).where(and_(Topic.id == str(topic_id), Topic.group_id == group.id))
        )
        if topic is None:
            raise NotFoundError("topic", str(topic_id))
        await db.delete(topic)
        await db.flush()
        await write_audit(
            db,
            actor_id=actor_id,
            institution_id=institution_id,
            action=AuditEvent.TOPIC_DELETED,
            resource_type="topic",
            resource_id=str(topic_id),
            metadata={"group_id": str(group.id)},
        )

    # ----- internal guards -----

    @staticmethod
    async def _require_membership(
        db: AsyncSession,
        *,
        group_id: uuid.UUID | str,
        user_id: uuid.UUID | str,
    ) -> GroupMember:
        row = await db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == str(group_id),
                GroupMember.user_id == str(user_id),
            )
        )
        if row is None:
            # Treat non-members as "group does not exist" for privacy.
            raise NotFoundError("group", str(group_id))
        return row

    @staticmethod
    async def _require_role(
        db: AsyncSession,
        *,
        group_id: uuid.UUID | str,
        user_id: uuid.UUID | str,
        allowed: tuple[str, ...],
    ) -> GroupMember:
        row = await GroupService._require_membership(db, group_id=group_id, user_id=user_id)
        if row.role not in allowed:
            raise PermissionDeniedError(f"Group role '{row.role}' not allowed for this action")
        return row
