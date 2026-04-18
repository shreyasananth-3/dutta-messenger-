"""Unit tests for UserService.

Tests hit a real Postgres through the rolled-back transaction pattern in
the root conftest — no mocks of the DB. Redis is mocked where it matters
(presence service has its own test file).
"""

from __future__ import annotations

import uuid
from datetime import UTC

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.users.services.user_service import UserService
from src.shared.exceptions import NotFoundError

# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


class TestGetById:
    @pytest.mark.asyncio
    async def test_happy_path(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        user = await UserService.get_by_id(
            db_session,
            user_id=uuid.UUID(admin_user.id),
            institution_id=uuid.UUID(institution.id),
        )
        assert user.id == admin_user.id
        assert user.email == admin_user.email

    @pytest.mark.asyncio
    async def test_unknown_id_returns_404(
        self, db_session: AsyncSession, institution: Institution
    ) -> None:
        with pytest.raises(NotFoundError):
            await UserService.get_by_id(
                db_session,
                user_id=uuid.uuid4(),
                institution_id=uuid.UUID(institution.id),
            )

    @pytest.mark.asyncio
    async def test_cross_tenant_returns_404_not_403(
        self,
        db_session: AsyncSession,
        institution: Institution,
        foreign_user: User,
    ) -> None:
        """User from institution A should never see institution B's users.

        Per `src/shared/security/tenant.py`, a cross-tenant lookup returns
        NOT_FOUND (not PERMISSION_DENIED) so attackers cannot enumerate
        other institutions' user IDs.
        """
        with pytest.raises(NotFoundError):
            await UserService.get_by_id(
                db_session,
                user_id=uuid.UUID(foreign_user.id),
                institution_id=uuid.UUID(institution.id),
            )

    @pytest.mark.asyncio
    async def test_soft_deleted_user_returns_404(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        from datetime import datetime

        admin_user.deleted_at = datetime.now(UTC)
        await db_session.flush()
        with pytest.raises(NotFoundError):
            await UserService.get_by_id(
                db_session,
                user_id=uuid.UUID(admin_user.id),
                institution_id=uuid.UUID(institution.id),
            )


# ---------------------------------------------------------------------------
# update_profile
# ---------------------------------------------------------------------------


class TestUpdateProfile:
    @pytest.mark.asyncio
    async def test_updates_allowed_fields(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        user = await UserService.update_profile(
            db_session,
            user_id=uuid.UUID(admin_user.id),
            institution_id=uuid.UUID(institution.id),
            full_name="New Name",
            bio="Updated bio",
            avatar_url="https://cdn.example.com/a.png",
            phone_number="+91-9999999999",
        )
        assert user.full_name == "New Name"
        assert user.bio == "Updated bio"
        assert user.avatar_url == "https://cdn.example.com/a.png"
        assert user.phone_number == "+91-9999999999"

    @pytest.mark.asyncio
    async def test_only_provided_fields_updated(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        original_name = admin_user.full_name
        user = await UserService.update_profile(
            db_session,
            user_id=uuid.UUID(admin_user.id),
            institution_id=uuid.UUID(institution.id),
            bio="Only bio changed",
        )
        assert user.full_name == original_name
        assert user.bio == "Only bio changed"

    @pytest.mark.asyncio
    async def test_noop_when_no_fields_passed(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        user = await UserService.update_profile(
            db_session,
            user_id=uuid.UUID(admin_user.id),
            institution_id=uuid.UUID(institution.id),
        )
        assert user.id == admin_user.id

    @pytest.mark.asyncio
    async def test_emits_one_audit_row(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        from sqlalchemy import text

        await UserService.update_profile(
            db_session,
            user_id=uuid.UUID(admin_user.id),
            institution_id=uuid.UUID(institution.id),
            bio="With audit",
        )
        # Audit row exists, tagged for this actor+institution+action.
        result = await db_session.execute(
            text(
                "SELECT count(*) FROM audit_logs "
                "WHERE actor_id=:a AND institution_id=:i AND action=:act"
            ),
            {
                "a": admin_user.id,
                "i": institution.id,
                "act": "user.profile.updated",
            },
        )
        assert result.scalar_one() == 1


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    @pytest.mark.asyncio
    async def test_returns_same_institution_users_by_name(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        other_user: User,
    ) -> None:
        # other_user full_name is "Other Person"
        results = await UserService.search(
            db_session,
            institution_id=uuid.UUID(institution.id),
            query="Other",
        )
        ids = {u.id for u in results}
        assert other_user.id in ids
        assert admin_user.id not in ids

    @pytest.mark.asyncio
    async def test_returns_by_email(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        results = await UserService.search(
            db_session,
            institution_id=uuid.UUID(institution.id),
            query="admin@users-test",
        )
        assert admin_user.id in {u.id for u in results}

    @pytest.mark.asyncio
    async def test_excludes_cross_tenant_users(
        self,
        db_session: AsyncSession,
        institution: Institution,
        foreign_user: User,
    ) -> None:
        results = await UserService.search(
            db_session,
            institution_id=uuid.UUID(institution.id),
            query="Foreign",
        )
        assert foreign_user.id not in {u.id for u in results}

    @pytest.mark.asyncio
    async def test_excludes_inactive_users(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        other_user: User,
    ) -> None:
        other_user.is_active = False
        await db_session.flush()
        results = await UserService.search(
            db_session,
            institution_id=uuid.UUID(institution.id),
            query="Other",
        )
        assert other_user.id not in {u.id for u in results}

    @pytest.mark.asyncio
    async def test_limit_respected(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        results = await UserService.search(
            db_session,
            institution_id=uuid.UUID(institution.id),
            query="a",
            limit=1,
        )
        assert len(results) <= 1


# ---------------------------------------------------------------------------
# Admin endpoint (PATCH /users/{id}/status) is intentionally NOT tested here.
# It's deferred to Stage 4b (ACL module); tests land alongside that code.
# See src/modules/users/services/user_service.py header comment for rationale.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


class TestSettings:
    @pytest.mark.asyncio
    async def test_get_or_create_seeds_defaults(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        settings = await UserService.get_or_create_settings(
            db_session,
            user_id=uuid.UUID(admin_user.id),
            institution_id=uuid.UUID(institution.id),
        )
        assert settings.notification_messages is True
        assert settings.notification_groups is True
        assert settings.theme == "system"
        assert settings.language == "en"

    @pytest.mark.asyncio
    async def test_get_or_create_is_idempotent(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        s1 = await UserService.get_or_create_settings(
            db_session,
            user_id=uuid.UUID(admin_user.id),
            institution_id=uuid.UUID(institution.id),
        )
        s2 = await UserService.get_or_create_settings(
            db_session,
            user_id=uuid.UUID(admin_user.id),
            institution_id=uuid.UUID(institution.id),
        )
        assert s1.id == s2.id

    @pytest.mark.asyncio
    async def test_update_settings_patches(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        settings = await UserService.update_settings(
            db_session,
            user_id=uuid.UUID(admin_user.id),
            institution_id=uuid.UUID(institution.id),
            theme="dark",
            language="hi",
            notification_sound=False,
        )
        assert settings.theme == "dark"
        assert settings.language == "hi"
        assert settings.notification_sound is False
        # Unchanged defaults stay
        assert settings.notification_messages is True

    @pytest.mark.asyncio
    async def test_update_settings_noop_when_empty(
        self, db_session: AsyncSession, institution: Institution, admin_user: User
    ) -> None:
        settings = await UserService.update_settings(
            db_session,
            user_id=uuid.UUID(admin_user.id),
            institution_id=uuid.UUID(institution.id),
        )
        assert settings is not None

    @pytest.mark.asyncio
    async def test_cross_tenant_settings_returns_404(
        self,
        db_session: AsyncSession,
        institution: Institution,
        foreign_user: User,
    ) -> None:
        with pytest.raises(NotFoundError):
            await UserService.get_or_create_settings(
                db_session,
                user_id=uuid.UUID(foreign_user.id),
                institution_id=uuid.UUID(institution.id),
            )
