"""Integration tests for `TokenService` — covers every branch & audit row."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.notifications.models.db_models import FcmToken
from src.modules.notifications.services.token_service import TokenService
from src.shared.exceptions import NotFoundError
from tests.modules.notifications.factories import (
    fresh_token_string,
    make_institution,
    make_token,
    make_user,
)


@pytest_asyncio.fixture
async def institution(db_session: AsyncSession) -> Institution:
    return await make_institution(db_session)


@pytest_asyncio.fixture
async def alice(db_session: AsyncSession, institution: Institution) -> User:
    return await make_user(db_session, institution=institution, email="alice@school.test")


@pytest_asyncio.fixture
async def bob(db_session: AsyncSession, institution: Institution) -> User:
    return await make_user(db_session, institution=institution, email="bob@school.test")


class TestRegisterToken:
    @pytest.mark.asyncio
    async def test_first_registration_persists_row_and_audit(
        self,
        db_session: AsyncSession,
        alice: User,
        institution: Institution,
    ) -> None:
        token_string = fresh_token_string()
        row, reused = await TokenService.register_token(
            db_session,
            user_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            token=token_string,
            device_name="iPhone 14",
            device_type="ios",
        )
        assert row.id
        assert row.user_id == alice.id
        assert row.is_active is True
        assert reused is False

        count = await db_session.scalar(
            text(
                "SELECT count(*) FROM audit_logs "
                "WHERE action = 'notification.token.registered' AND actor_id = :uid"
            ),
            {"uid": alice.id},
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_same_user_same_token_is_idempotent(
        self,
        db_session: AsyncSession,
        alice: User,
        institution: Institution,
    ) -> None:
        token_string = fresh_token_string()
        first, reused_first = await TokenService.register_token(
            db_session,
            user_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            token=token_string,
            device_name="iPhone",
            device_type="ios",
        )
        second, reused_second = await TokenService.register_token(
            db_session,
            user_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            token=token_string,
            device_name="iPhone (renamed)",
            device_type="ios",
        )
        assert reused_first is False
        assert reused_second is True
        assert first.id == second.id
        assert second.device_name == "iPhone (renamed)"

        count = await db_session.scalar(
            text("SELECT count(*) FROM fcm_tokens WHERE user_id = :uid"),
            {"uid": alice.id},
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_reregister_reactivates_after_revoke(
        self,
        db_session: AsyncSession,
        alice: User,
        institution: Institution,
    ) -> None:
        token_string = fresh_token_string()
        row, _ = await TokenService.register_token(
            db_session,
            user_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            token=token_string,
            device_name="iPhone",
            device_type="ios",
        )
        await TokenService.revoke_token(
            db_session,
            user_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            token_id=uuid.UUID(row.id),
        )
        refreshed, reused = await TokenService.register_token(
            db_session,
            user_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            token=token_string,
            device_name="iPhone",
            device_type="ios",
        )
        assert reused is True
        assert refreshed.is_active is True

    @pytest.mark.asyncio
    async def test_token_rebound_to_another_user(
        self,
        db_session: AsyncSession,
        alice: User,
        bob: User,
        institution: Institution,
    ) -> None:
        token_string = fresh_token_string()
        alice_row, _ = await TokenService.register_token(
            db_session,
            user_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            token=token_string,
            device_name="Shared",
            device_type="android",
        )
        bob_row, reused = await TokenService.register_token(
            db_session,
            user_id=uuid.UUID(bob.id),
            institution_id=uuid.UUID(institution.id),
            token=token_string,
            device_name="Shared",
            device_type="android",
        )
        assert reused is False
        assert bob_row.id != alice_row.id
        total = await db_session.scalar(
            text("SELECT count(*) FROM fcm_tokens WHERE token = :t"),
            {"t": token_string},
        )
        assert total == 1


class TestRevokeToken:
    @pytest.mark.asyncio
    async def test_revoke_own_token_deactivates_and_audits(
        self,
        db_session: AsyncSession,
        alice: User,
        institution: Institution,
    ) -> None:
        row = await make_token(db_session, user=alice, institution=institution)
        await TokenService.revoke_token(
            db_session,
            user_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            token_id=uuid.UUID(row.id),
        )
        refreshed = await db_session.scalar(select(FcmToken).where(FcmToken.id == row.id))
        assert refreshed is not None
        assert refreshed.is_active is False

    @pytest.mark.asyncio
    async def test_revoke_unknown_token_raises_404(
        self,
        db_session: AsyncSession,
        alice: User,
        institution: Institution,
    ) -> None:
        with pytest.raises(NotFoundError):
            await TokenService.revoke_token(
                db_session,
                user_id=uuid.UUID(alice.id),
                institution_id=uuid.UUID(institution.id),
                token_id=uuid.uuid4(),
            )

    @pytest.mark.asyncio
    async def test_revoke_other_users_token_is_404_not_403(
        self,
        db_session: AsyncSession,
        alice: User,
        bob: User,
        institution: Institution,
    ) -> None:
        row = await make_token(db_session, user=alice, institution=institution)
        with pytest.raises(NotFoundError):
            await TokenService.revoke_token(
                db_session,
                user_id=uuid.UUID(bob.id),
                institution_id=uuid.UUID(institution.id),
                token_id=uuid.UUID(row.id),
            )


class TestCrossTenantFuzz:
    @pytest.mark.asyncio
    async def test_cross_institution_revoke_returns_404(self, db_session: AsyncSession) -> None:
        inst_a = await make_institution(db_session, name="Inst-A")
        inst_b = await make_institution(db_session, name="Inst-B")
        user_a = await make_user(db_session, institution=inst_a)
        user_b = await make_user(db_session, institution=inst_b)
        token = await make_token(db_session, user=user_a, institution=inst_a)

        with pytest.raises(NotFoundError):
            await TokenService.revoke_token(
                db_session,
                user_id=uuid.UUID(user_b.id),
                institution_id=uuid.UUID(inst_b.id),
                token_id=uuid.UUID(token.id),
            )


class TestListActiveTokens:
    @pytest.mark.asyncio
    async def test_returns_only_active_for_user(
        self,
        db_session: AsyncSession,
        alice: User,
        institution: Institution,
    ) -> None:
        t1 = await make_token(db_session, user=alice, institution=institution)
        t2 = await make_token(db_session, user=alice, institution=institution)
        await TokenService.revoke_token(
            db_session,
            user_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            token_id=uuid.UUID(t1.id),
        )
        active = await TokenService.list_active_tokens(db_session, user_id=uuid.UUID(alice.id))
        assert [row.id for row in active] == [t2.id]


class TestDeactivateByString:
    @pytest.mark.asyncio
    async def test_fcm_unregistered_deactivates_and_audits(
        self,
        db_session: AsyncSession,
        alice: User,
        institution: Institution,
    ) -> None:
        row = await make_token(db_session, user=alice, institution=institution)
        await TokenService.deactivate_by_string(
            db_session,
            institution_id=uuid.UUID(institution.id),
            token=row.token,
        )
        refreshed = await db_session.scalar(select(FcmToken).where(FcmToken.id == row.id))
        assert refreshed is not None
        assert refreshed.is_active is False

    @pytest.mark.asyncio
    async def test_unknown_token_string_is_noop(
        self, db_session: AsyncSession, institution: Institution
    ) -> None:
        await TokenService.deactivate_by_string(
            db_session,
            institution_id=uuid.UUID(institution.id),
            token=f"nonexistent-{uuid.uuid4().hex}",
        )

    @pytest.mark.asyncio
    async def test_already_inactive_is_noop(
        self,
        db_session: AsyncSession,
        alice: User,
        institution: Institution,
    ) -> None:
        row = await make_token(db_session, user=alice, institution=institution)
        await TokenService.revoke_token(
            db_session,
            user_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            token_id=uuid.UUID(row.id),
        )
        await TokenService.deactivate_by_string(
            db_session,
            institution_id=uuid.UUID(institution.id),
            token=row.token,
        )
