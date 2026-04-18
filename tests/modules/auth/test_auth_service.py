"""Integration tests for AuthService — covers every business rule and branch."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import (
    Institution,
    RefreshToken,
    User,
    UserInvitation,
)
from src.modules.auth.services.auth_service import AuthService
from src.shared.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from src.shared.utils.datetime_utils import get_utc_now

VALID_PASSWORD = "Sup3rStr0ng!"


@pytest_asyncio.fixture
async def institution(db_session: AsyncSession) -> Institution:
    inst = await AuthService.create_institution(
        db_session,
        name=f"School {uuid.uuid4().hex[:8]}",
        domain=f"{uuid.uuid4().hex[:6]}.test",
    )
    await db_session.flush()
    return inst


@pytest_asyncio.fixture
async def existing_user(db_session: AsyncSession, institution: Institution) -> User:
    return await AuthService.register_user(
        db_session,
        institution_id=institution.id,
        email="alice@school.test",
        password=VALID_PASSWORD,
        full_name="Alice Example",
    )


# --- password helpers --------------------------------------------------------


class TestPasswordHelpers:
    def test_hash_then_verify_true(self) -> None:
        h = AuthService.hash_password(VALID_PASSWORD)
        assert h != VALID_PASSWORD
        assert AuthService.verify_password(VALID_PASSWORD, h) is True

    def test_verify_wrong_password_false(self) -> None:
        h = AuthService.hash_password(VALID_PASSWORD)
        assert AuthService.verify_password("Wrong1Pass!", h) is False


# --- create_institution ------------------------------------------------------


class TestCreateInstitution:
    @pytest.mark.asyncio
    async def test_creates_with_overrides(self, db_session: AsyncSession) -> None:
        inst = await AuthService.create_institution(
            db_session,
            name=f"Inst {uuid.uuid4().hex[:6]}",
            description="A school",
            domain="school.test.org",
            subscription_tier="paid",
            max_users=500,
            max_groups=100,
        )
        assert inst.id is not None
        assert inst.subscription_tier == "paid"
        assert inst.max_users == 500
        assert inst.max_groups == 100
        assert inst.description == "A school"

    @pytest.mark.asyncio
    async def test_duplicate_name_raises_conflict(
        self, db_session: AsyncSession, institution: Institution
    ) -> None:
        with pytest.raises(ConflictError):
            await AuthService.create_institution(db_session, name=institution.name)


# --- register_user -----------------------------------------------------------


class TestRegisterUser:
    @pytest.mark.asyncio
    async def test_happy_path(self, db_session: AsyncSession, institution: Institution) -> None:
        user = await AuthService.register_user(
            db_session,
            institution_id=institution.id,
            email="bob@school.test",
            password=VALID_PASSWORD,
            full_name="Bob Tester",
            phone_number="+1-415-555-1234",
        )
        assert user.email == "bob@school.test"
        assert user.password_hash != VALID_PASSWORD
        assert user.is_active is True
        assert user.status == "offline"

    @pytest.mark.asyncio
    async def test_unknown_institution_404(self, db_session: AsyncSession) -> None:
        with pytest.raises(NotFoundError):
            await AuthService.register_user(
                db_session,
                institution_id=str(uuid.uuid4()),
                email="x@y.com",
                password=VALID_PASSWORD,
                full_name="X Y",
            )

    @pytest.mark.asyncio
    async def test_duplicate_email_conflict(
        self, db_session: AsyncSession, institution: Institution, existing_user: User
    ) -> None:
        with pytest.raises(ConflictError):
            await AuthService.register_user(
                db_session,
                institution_id=institution.id,
                email=existing_user.email,
                password=VALID_PASSWORD,
                full_name="Other",
            )

    @pytest.mark.asyncio
    async def test_invalid_email_validation(
        self, db_session: AsyncSession, institution: Institution
    ) -> None:
        with pytest.raises(ValidationError):
            await AuthService.register_user(
                db_session,
                institution_id=institution.id,
                email="not-an-email",
                password=VALID_PASSWORD,
                full_name="X",
            )

    @pytest.mark.asyncio
    async def test_unicode_full_name_accepted(
        self, db_session: AsyncSession, institution: Institution
    ) -> None:
        user = await AuthService.register_user(
            db_session,
            institution_id=institution.id,
            email="charlie@school.test",
            password=VALID_PASSWORD,
            full_name="नमस्ते 你好 😀",
        )
        assert user.full_name == "नमस्ते 你好 😀"


# --- login -------------------------------------------------------------------


class TestLogin:
    @pytest.mark.asyncio
    async def test_happy_path_returns_user_and_tokens(
        self, db_session: AsyncSession, existing_user: User
    ) -> None:
        user, access, refresh = await AuthService.login(
            db_session, email=existing_user.email, password=VALID_PASSWORD
        )
        assert user.id == existing_user.id
        assert isinstance(access, str) and access.count(".") == 2
        assert isinstance(refresh, str) and refresh.count(".") == 2
        # last_seen_at updated
        assert user.last_seen_at is not None
        # refresh token row persisted
        rows = (
            (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)))
            .scalars()
            .all()
        )
        assert len(rows) >= 1

    @pytest.mark.asyncio
    async def test_wrong_password_401(self, db_session: AsyncSession, existing_user: User) -> None:
        with pytest.raises(AuthenticationError):
            await AuthService.login(db_session, email=existing_user.email, password="Wrong1Pass!")

    @pytest.mark.asyncio
    async def test_unknown_email_401(self, db_session: AsyncSession) -> None:
        with pytest.raises(AuthenticationError):
            await AuthService.login(db_session, email="ghost@nowhere.test", password=VALID_PASSWORD)

    @pytest.mark.asyncio
    async def test_inactive_user_blocked(
        self, db_session: AsyncSession, existing_user: User
    ) -> None:
        existing_user.is_active = False
        await db_session.flush()
        with pytest.raises(AuthenticationError, match="inactive"):
            await AuthService.login(db_session, email=existing_user.email, password=VALID_PASSWORD)

    @pytest.mark.asyncio
    async def test_deleted_user_blocked(
        self, db_session: AsyncSession, existing_user: User
    ) -> None:
        existing_user.deleted_at = get_utc_now()
        await db_session.flush()
        with pytest.raises(AuthenticationError, match="deleted"):
            await AuthService.login(db_session, email=existing_user.email, password=VALID_PASSWORD)

    @pytest.mark.asyncio
    async def test_email_normalised_before_lookup(
        self, db_session: AsyncSession, existing_user: User
    ) -> None:
        user, _, _ = await AuthService.login(
            db_session, email=existing_user.email.upper(), password=VALID_PASSWORD
        )
        assert user.id == existing_user.id

    @pytest.mark.asyncio
    async def test_institution_id_filter_used(
        self, db_session: AsyncSession, existing_user: User
    ) -> None:
        # Wrong institution_id → user not found path → AuthenticationError
        with pytest.raises(AuthenticationError):
            await AuthService.login(
                db_session,
                email=existing_user.email,
                password=VALID_PASSWORD,
                institution_id=str(uuid.uuid4()),
            )


# --- refresh_access_token ----------------------------------------------------


class TestRefreshAccessToken:
    @pytest.mark.asyncio
    async def test_happy_path(self, db_session: AsyncSession, existing_user: User) -> None:
        access, refresh = await AuthService.refresh_access_token(
            db_session,
            user_id=uuid.UUID(existing_user.id),
            institution_id=uuid.UUID(existing_user.institution_id),
        )
        assert access and refresh
        rows = (
            (
                await db_session.execute(
                    select(RefreshToken).where(RefreshToken.user_id == existing_user.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) >= 1

    @pytest.mark.asyncio
    async def test_unknown_user_404(self, db_session: AsyncSession) -> None:
        with pytest.raises(NotFoundError):
            await AuthService.refresh_access_token(
                db_session,
                user_id=uuid.uuid4(),
                institution_id=uuid.uuid4(),
            )

    @pytest.mark.asyncio
    async def test_inactive_user_blocked(
        self, db_session: AsyncSession, existing_user: User
    ) -> None:
        existing_user.is_active = False
        await db_session.flush()
        with pytest.raises(AuthenticationError):
            await AuthService.refresh_access_token(
                db_session,
                user_id=uuid.UUID(existing_user.id),
                institution_id=uuid.UUID(existing_user.institution_id),
            )


# --- create_invitation -------------------------------------------------------


class TestCreateInvitation:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        db_session: AsyncSession,
        institution: Institution,
        existing_user: User,
    ) -> None:
        inv = await AuthService.create_invitation(
            db_session,
            institution_id=institution.id,
            email="newbie@school.test",
            invited_by_user_id=existing_user.id,
        )
        assert inv.token and len(inv.token) > 20
        assert inv.email == "newbie@school.test"
        assert inv.expires_at > get_utc_now()
        assert inv.accepted_at is None

    @pytest.mark.asyncio
    async def test_unknown_institution_404(
        self, db_session: AsyncSession, existing_user: User
    ) -> None:
        with pytest.raises(NotFoundError, match="Institution"):
            await AuthService.create_invitation(
                db_session,
                institution_id=str(uuid.uuid4()),
                email="newbie@school.test",
                invited_by_user_id=existing_user.id,
            )

    @pytest.mark.asyncio
    async def test_unknown_inviter_404(
        self, db_session: AsyncSession, institution: Institution
    ) -> None:
        with pytest.raises(NotFoundError, match="User"):
            await AuthService.create_invitation(
                db_session,
                institution_id=institution.id,
                email="newbie@school.test",
                invited_by_user_id=str(uuid.uuid4()),
            )

    @pytest.mark.asyncio
    async def test_user_already_in_institution_conflict(
        self,
        db_session: AsyncSession,
        institution: Institution,
        existing_user: User,
    ) -> None:
        with pytest.raises(ConflictError):
            await AuthService.create_invitation(
                db_session,
                institution_id=institution.id,
                email=existing_user.email,
                invited_by_user_id=existing_user.id,
            )

    @pytest.mark.asyncio
    async def test_invalid_email_validation(
        self,
        db_session: AsyncSession,
        institution: Institution,
        existing_user: User,
    ) -> None:
        with pytest.raises(ValidationError):
            await AuthService.create_invitation(
                db_session,
                institution_id=institution.id,
                email="not-an-email",
                invited_by_user_id=existing_user.id,
            )


# --- accept_invitation -------------------------------------------------------


class TestAcceptInvitation:
    @pytest.mark.asyncio
    async def test_happy_path_creates_user_and_marks_accepted(
        self,
        db_session: AsyncSession,
        institution: Institution,
        existing_user: User,
    ) -> None:
        inv = await AuthService.create_invitation(
            db_session,
            institution_id=institution.id,
            email="invited@school.test",
            invited_by_user_id=existing_user.id,
        )
        user = await AuthService.accept_invitation(
            db_session,
            token=inv.token,
            password=VALID_PASSWORD,
            full_name="Invited User",
        )
        assert user.email == "invited@school.test"
        # invitation row updated
        loaded = (
            (await db_session.execute(select(UserInvitation).where(UserInvitation.id == inv.id)))
            .scalars()
            .first()
        )
        assert loaded is not None
        assert loaded.accepted_at is not None
        assert loaded.accepted_user_id == user.id

    @pytest.mark.asyncio
    async def test_unknown_token_404(self, db_session: AsyncSession) -> None:
        with pytest.raises(NotFoundError):
            await AuthService.accept_invitation(
                db_session,
                token="does-not-exist",
                password=VALID_PASSWORD,
                full_name="X Y",
            )

    @pytest.mark.asyncio
    async def test_expired_invitation_validation_error(
        self,
        db_session: AsyncSession,
        institution: Institution,
        existing_user: User,
    ) -> None:
        inv = await AuthService.create_invitation(
            db_session,
            institution_id=institution.id,
            email="late@school.test",
            invited_by_user_id=existing_user.id,
        )
        inv.expires_at = get_utc_now() - timedelta(hours=1)
        await db_session.flush()
        with pytest.raises(ValidationError, match="expired"):
            await AuthService.accept_invitation(
                db_session,
                token=inv.token,
                password=VALID_PASSWORD,
                full_name="Late User",
            )

    @pytest.mark.asyncio
    async def test_already_accepted_conflict(
        self,
        db_session: AsyncSession,
        institution: Institution,
        existing_user: User,
    ) -> None:
        inv = await AuthService.create_invitation(
            db_session,
            institution_id=institution.id,
            email="dup@school.test",
            invited_by_user_id=existing_user.id,
        )
        await AuthService.accept_invitation(
            db_session,
            token=inv.token,
            password=VALID_PASSWORD,
            full_name="First Time",
        )
        with pytest.raises(ConflictError):
            await AuthService.accept_invitation(
                db_session,
                token=inv.token,
                password=VALID_PASSWORD,
                full_name="Second Time",
            )


# --- change_password ---------------------------------------------------------


class TestChangePassword:
    @pytest.mark.asyncio
    async def test_happy_path(self, db_session: AsyncSession, existing_user: User) -> None:
        new_pw = "EvenStr0nger!"
        old_hash = existing_user.password_hash
        updated = await AuthService.change_password(
            db_session,
            user_id=existing_user.id,
            current_password=VALID_PASSWORD,
            new_password=new_pw,
        )
        assert updated.password_hash != old_hash
        assert AuthService.verify_password(new_pw, updated.password_hash)

    @pytest.mark.asyncio
    async def test_unknown_user_404(self, db_session: AsyncSession) -> None:
        with pytest.raises(NotFoundError):
            await AuthService.change_password(
                db_session,
                user_id=str(uuid.uuid4()),
                current_password=VALID_PASSWORD,
                new_password="EvenStr0nger!",
            )

    @pytest.mark.asyncio
    async def test_wrong_current_password_401(
        self, db_session: AsyncSession, existing_user: User
    ) -> None:
        with pytest.raises(AuthenticationError):
            await AuthService.change_password(
                db_session,
                user_id=existing_user.id,
                current_password="Wrong1Pass!",
                new_password="EvenStr0nger!",
            )

    @pytest.mark.asyncio
    async def test_weak_new_password_rejected(
        self, db_session: AsyncSession, existing_user: User
    ) -> None:
        with pytest.raises(ValidationError):
            await AuthService.change_password(
                db_session,
                user_id=existing_user.id,
                current_password=VALID_PASSWORD,
                new_password="weak",
            )
