"""Service layer for the users module.

All profile, search, status, and settings logic lives here. Routes are thin
adapters over these methods (≤ 15 lines per handler per CLAUDE.md).

Every mutation:
  1. Uses `tenant_scoped_query(...)` or `assert_same_institution(...)` from
     `src/shared/security/tenant.py` to enforce the institution boundary.
  2. Emits exactly one `write_audit(...)` inside the same DB transaction,
     per `docs/design/tenant-isolation.md`.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.users.models.db_models import User, UserSettings
from src.modules.users.services import presence_service
from src.shared.exceptions import NotFoundError
from src.shared.security.audit import AuditEvent, write_audit
from src.shared.security.tenant import tenant_scoped_query

logger = structlog.get_logger()

_SEARCH_COLUMNS = (User.full_name, User.email)


class UserService:
    """Static-method collection; no per-instance state."""

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    @staticmethod
    async def get_by_id(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        institution_id: uuid.UUID,
    ) -> User:
        """Fetch a user within a given institution or raise 404.

        Used by `/users/me` and `/users/{id}`. Cross-institution lookups
        return 404 (not 403) per `src/shared/security/tenant.py`'s design
        — an attacker must not be able to enumerate other institutions'
        user IDs.
        """
        stmt = tenant_scoped_query(User, institution_id).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if user is None or user.deleted_at is not None:
            raise NotFoundError("user", str(user_id))
        return user

    @staticmethod
    async def update_profile(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        institution_id: uuid.UUID,
        full_name: str | None = None,
        avatar_url: str | None = None,
        bio: str | None = None,
        phone_number: str | None = None,
    ) -> User:
        """Update profile fields the user is allowed to edit.

        Email + status are intentionally excluded — see MODULE.md's
        "Profile Fields" table.
        """
        user = await UserService.get_by_id(db, user_id=user_id, institution_id=institution_id)

        updated_fields: dict[str, Any] = {}
        if full_name is not None:
            user.full_name = full_name
            updated_fields["full_name"] = full_name
        if avatar_url is not None:
            user.avatar_url = avatar_url
            updated_fields["avatar_url"] = avatar_url
        if bio is not None:
            user.bio = bio
            updated_fields["bio"] = bio
        if phone_number is not None:
            user.phone_number = phone_number
            updated_fields["phone_number"] = phone_number

        if not updated_fields:
            return user

        await db.flush()
        await write_audit(
            db,
            actor_id=user_id,
            institution_id=institution_id,
            action=AuditEvent.USER_PROFILE_UPDATED,
            resource_type="user",
            resource_id=user_id,
            metadata={"fields": sorted(updated_fields.keys())},
        )
        return user

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @staticmethod
    async def search(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID,
        query: str,
        limit: int = 20,
    ) -> list[User]:
        """Search users in the same institution by name or email.

        Uses `ILIKE` against the trigram-indexed `full_name` (migration
        0004 adds the GIN index). Email matches use `ILIKE` too; the
        unique index on `(institution_id, email)` keeps it selective.
        """
        pattern = f"%{query}%"
        stmt = (
            tenant_scoped_query(User, institution_id)
            .where(User.deleted_at.is_(None))
            .where(User.is_active.is_(True))
            .where(or_(User.full_name.ilike(pattern), User.email.ilike(pattern)))
            .order_by(User.full_name)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Admin: activate / deactivate
    #
    # Deliberately NOT implemented in Stage 4a. MODULE.md's
    # `PATCH /users/{id}/status` requires the `institution.manage_users`
    # permission, which only exists once the ACL module (Stage 4b) lands.
    # A temporary "first-registered user is admin" heuristic was tried
    # during development but turned out to be non-deterministic (users
    # registered in the same transaction share `created_at` via Postgres
    # transaction-scoped `NOW()`, so the admin candidate tiebroke on
    # random UUID ordering). Rather than ship a flaky heuristic, the
    # endpoint is deferred to Stage 4b where it can be wired to the real
    # permission check. See `docs/NEXT_SESSION.md` for the follow-up.
    #
    # The `USER_SUSPENDED` and `USER_REACTIVATED` `AuditEvent` members
    # DO ship today — ACL can use them without a subsequent shared-infra
    # change.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    @staticmethod
    async def get_or_create_settings(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        institution_id: uuid.UUID,
    ) -> UserSettings:
        """Fetch settings row for a user, lazily creating defaults.

        The migration does NOT back-fill existing users; we seed on first
        read so shipping this module is always zero-downtime regardless
        of how many users the institution already has.
        """
        # Tenant check: confirm the user belongs to this institution before
        # reading or creating settings for them.
        await UserService.get_by_id(db, user_id=user_id, institution_id=institution_id)

        stmt = select(UserSettings).where(UserSettings.user_id == str(user_id))
        result = await db.execute(stmt)
        settings = result.scalar_one_or_none()
        if settings is not None:
            return settings

        settings = UserSettings(user_id=str(user_id))
        db.add(settings)
        await db.flush()
        return settings

    @staticmethod
    async def update_settings(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        institution_id: uuid.UUID,
        notification_messages: bool | None = None,
        notification_groups: bool | None = None,
        notification_sound: bool | None = None,
        theme: str | None = None,
        language: str | None = None,
    ) -> UserSettings:
        """Patch the caller's own settings."""
        settings = await UserService.get_or_create_settings(
            db, user_id=user_id, institution_id=institution_id
        )

        updated_fields: dict[str, Any] = {}
        if notification_messages is not None:
            settings.notification_messages = notification_messages
            updated_fields["notification_messages"] = notification_messages
        if notification_groups is not None:
            settings.notification_groups = notification_groups
            updated_fields["notification_groups"] = notification_groups
        if notification_sound is not None:
            settings.notification_sound = notification_sound
            updated_fields["notification_sound"] = notification_sound
        if theme is not None:
            settings.theme = theme
            updated_fields["theme"] = theme
        if language is not None:
            settings.language = language
            updated_fields["language"] = language

        if not updated_fields:
            return settings

        await db.flush()
        await write_audit(
            db,
            actor_id=user_id,
            institution_id=institution_id,
            action=AuditEvent.USER_SETTINGS_UPDATED,
            resource_type="user_settings",
            resource_id=settings.id,
            metadata={"fields": sorted(updated_fields.keys())},
        )
        return settings

    # ------------------------------------------------------------------
    # Convenience: presence merge
    # ------------------------------------------------------------------

    @staticmethod
    async def annotate_online(users: list[User]) -> dict[uuid.UUID, bool]:
        """Return `{user_id: is_online}` for a list of User rows.

        Callers then merge this into the response model. Kept as a thin
        wrapper so route code doesn't reach into `presence_service`.
        """
        return await presence_service.get_online_map(u.id for u in users)
