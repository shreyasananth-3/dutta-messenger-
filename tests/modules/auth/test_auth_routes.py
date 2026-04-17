"""HTTP-level tests for auth routes."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.auth.services.auth_service import AuthService
from src.shared.middleware.auth import create_access_token

API = "/api/v1"
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
async def registered_user(
    db_session: AsyncSession, institution: Institution
) -> User:
    user = await AuthService.register_user(
        db_session,
        institution_id=institution.id,
        email="route-tester@school.test",
        password=VALID_PASSWORD,
        full_name="Route Tester",
    )
    await db_session.flush()
    return user


# --- POST /api/v1/institutions ----------------------------------------------


class TestCreateInstitutionRoute:
    @pytest.mark.asyncio
    async def test_happy_path_201(self, client: AsyncClient) -> None:
        r = await client.post(
            f"{API}/institutions",
            json={"name": f"NewSchool-{uuid.uuid4().hex[:8]}"},
        )
        assert r.status_code == 201
        body = r.json()
        assert "data" in body
        assert body["data"]["name"].startswith("NewSchool-")

    @pytest.mark.asyncio
    async def test_duplicate_name_409(
        self, client: AsyncClient, institution: Institution
    ) -> None:
        r = await client.post(
            f"{API}/institutions", json={"name": institution.name}
        )
        assert r.status_code == 409
        assert r.json()["detail"]["error"]["code"] == "CONFLICT"

    @pytest.mark.asyncio
    async def test_missing_name_422(self, client: AsyncClient) -> None:
        r = await client.post(f"{API}/institutions", json={})
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_max_users_out_of_range_422(self, client: AsyncClient) -> None:
        r = await client.post(
            f"{API}/institutions", json={"name": "X", "max_users": 9}
        )
        assert r.status_code == 422


# --- POST /api/v1/auth/register ---------------------------------------------


class TestRegisterRoute:
    @pytest.mark.asyncio
    async def test_no_invitation_token_400(self, client: AsyncClient) -> None:
        r = await client.post(
            f"{API}/auth/register",
            json={
                "email": "x@y.test",
                "password": VALID_PASSWORD,
                "full_name": "X Y",
            },
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_with_invitation_token_201(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        institution: Institution,
        registered_user: User,
    ) -> None:
        invitation = await AuthService.create_invitation(
            db_session,
            institution_id=institution.id,
            email="invitee@school.test",
            invited_by_user_id=registered_user.id,
        )
        await db_session.flush()
        r = await client.post(
            f"{API}/auth/register",
            json={
                "email": "invitee@school.test",
                "password": VALID_PASSWORD,
                "full_name": "Invitee",
                "invitation_token": invitation.token,
            },
        )
        assert r.status_code == 201
        assert r.json()["data"]["user"]["email"] == "invitee@school.test"

    @pytest.mark.asyncio
    async def test_unknown_invitation_token_404(self, client: AsyncClient) -> None:
        r = await client.post(
            f"{API}/auth/register",
            json={
                "email": "x@y.test",
                "password": VALID_PASSWORD,
                "full_name": "X Y",
                "invitation_token": "does-not-exist",
            },
        )
        assert r.status_code == 404


# --- POST /api/v1/auth/login ------------------------------------------------


class TestLoginRoute:
    @pytest.mark.asyncio
    async def test_happy_path(
        self, client: AsyncClient, registered_user: User
    ) -> None:
        r = await client.post(
            f"{API}/auth/login",
            json={"email": registered_user.email, "password": VALID_PASSWORD},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["access_token"].count(".") == 2
        assert body["data"]["refresh_token"].count(".") == 2
        assert body["data"]["user"]["email"] == registered_user.email
        assert body["data"]["expires_in_seconds"] > 0

    @pytest.mark.asyncio
    async def test_wrong_password_401(
        self, client: AsyncClient, registered_user: User
    ) -> None:
        r = await client.post(
            f"{API}/auth/login",
            json={"email": registered_user.email, "password": "Wrong1Pass!"},
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_user_401(self, client: AsyncClient) -> None:
        r = await client.post(
            f"{API}/auth/login",
            json={"email": "ghost@nowhere.test", "password": VALID_PASSWORD},
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_password_422(self, client: AsyncClient) -> None:
        r = await client.post(f"{API}/auth/login", json={"email": "x@y.test"})
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_unicode_email_normalised(
        self, client: AsyncClient, registered_user: User
    ) -> None:
        r = await client.post(
            f"{API}/auth/login",
            json={
                "email": registered_user.email.upper(),
                "password": VALID_PASSWORD,
            },
        )
        assert r.status_code == 200


# --- POST /api/v1/auth/refresh ----------------------------------------------


class TestRefreshRoute:
    @pytest.mark.asyncio
    async def test_no_auth_header_403(self, client: AsyncClient) -> None:
        r = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": "anything"}
        )
        # HTTPBearer auto-error returns 401 or 403 depending on FastAPI version
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        client: AsyncClient,
        registered_user: User,
        auth_headers,
    ) -> None:
        headers = auth_headers(registered_user)
        r = await client.post(
            f"{API}/auth/refresh",
            json={"refresh_token": "ignored-by-impl"},
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["access_token"].count(".") == 2

    @pytest.mark.asyncio
    async def test_user_not_in_db_404(
        self, client: AsyncClient
    ) -> None:
        # Token for a user that doesn't exist in DB → service raises NotFoundError
        token = create_access_token(uuid.uuid4(), uuid.uuid4())
        r = await client.post(
            f"{API}/auth/refresh",
            json={"refresh_token": "x"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404


# --- POST /api/v1/auth/invite -----------------------------------------------


class TestInviteRoute:
    @pytest.mark.asyncio
    async def test_no_auth_403(self, client: AsyncClient) -> None:
        r = await client.post(
            f"{API}/auth/invite", json={"email": "x@school.test"}
        )
        # HTTPBearer auto-error returns 401 or 403 depending on FastAPI version
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_happy_path_201(
        self,
        client: AsyncClient,
        registered_user: User,
        auth_headers,
    ) -> None:
        r = await client.post(
            f"{API}/auth/invite",
            json={"email": "fresh-invitee@school.test"},
            headers=auth_headers(registered_user),
        )
        assert r.status_code == 201
        body = r.json()
        assert body["data"]["invitation"]["email"] == "fresh-invitee@school.test"

    @pytest.mark.asyncio
    async def test_invite_existing_member_conflict(
        self,
        client: AsyncClient,
        registered_user: User,
        auth_headers,
    ) -> None:
        r = await client.post(
            f"{API}/auth/invite",
            json={"email": registered_user.email},
            headers=auth_headers(registered_user),
        )
        assert r.status_code == 409


# --- POST /api/v1/auth/change-password --------------------------------------


class TestChangePasswordRoute:
    @pytest.mark.asyncio
    async def test_no_auth_403(self, client: AsyncClient) -> None:
        r = await client.post(
            f"{API}/auth/change-password",
            json={
                "current_password": VALID_PASSWORD,
                "new_password": "EvenStr0nger!",
            },
        )
        # HTTPBearer auto-error returns 401 or 403 depending on FastAPI version
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        client: AsyncClient,
        registered_user: User,
        auth_headers,
    ) -> None:
        r = await client.post(
            f"{API}/auth/change-password",
            json={
                "current_password": VALID_PASSWORD,
                "new_password": "BrandN3wPass!",
            },
            headers=auth_headers(registered_user),
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_wrong_current_401(
        self,
        client: AsyncClient,
        registered_user: User,
        auth_headers,
    ) -> None:
        r = await client.post(
            f"{API}/auth/change-password",
            json={
                "current_password": "Wrong1Pass!",
                "new_password": "BrandN3wPass!",
            },
            headers=auth_headers(registered_user),
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_weak_new_password_422_or_400(
        self,
        client: AsyncClient,
        registered_user: User,
        auth_headers,
    ) -> None:
        # Pydantic's min_length=8 catches this first → 422
        r = await client.post(
            f"{API}/auth/change-password",
            json={"current_password": VALID_PASSWORD, "new_password": "weak"},
            headers=auth_headers(registered_user),
        )
        assert r.status_code in (400, 422)
