"""Pytest fixtures for the ACL module tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.acl.router import router as acl_router
from src.modules.acl.services.acl_service import ACLService
from src.modules.auth.models.db_models import Institution, User
from src.modules.auth.services.auth_service import AuthService
from src.shared.database import get_db
from src.shared.exceptions import AppException

VALID_PASSWORD = "Sup3rStr0ng!"


def _build_test_app() -> FastAPI:
    """Minimal FastAPI app with only the ACL router mounted."""
    app = FastAPI(title="DuttaMessenger (acl test app)")

    @app.exception_handler(AppException)
    async def _app_exc_handler(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    app.include_router(acl_router, prefix="/api/v1")
    return app


@pytest_asyncio.fixture
async def acl_client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    """HTTPX client wired to the acl app + test DB session."""
    app = _build_test_app()

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def make_institution(db_session: AsyncSession, *, name: str | None = None) -> Institution:
    inst = await AuthService.create_institution(
        db_session,
        name=name or f"Inst-{uuid.uuid4().hex[:8]}",
        domain=f"{uuid.uuid4().hex[:6]}.test",
    )
    await ACLService.seed_institution_roles(db_session, institution_id=inst.id)
    await db_session.flush()
    return inst


async def make_user(
    db_session: AsyncSession,
    *,
    institution: Institution,
    email: str | None = None,
    role_name: str | None = None,
) -> User:
    user = await AuthService.register_user(
        db_session,
        institution_id=institution.id,
        email=email or f"u-{uuid.uuid4().hex[:6]}@x.test",
        password=VALID_PASSWORD,
        full_name="Test User",
    )
    await db_session.flush()
    if role_name:
        roles = await ACLService.list_roles(db_session, institution_id=institution.id)
        by_name = {r.name: r for r in roles}
        await ACLService.assign_role(
            db_session,
            institution_id=institution.id,
            user_id=user.id,
            role_id=by_name[role_name].id,
            assigned_by=user.id,
        )
        await db_session.flush()
    return user


@pytest_asyncio.fixture
async def institution(db_session: AsyncSession) -> Institution:
    return await make_institution(db_session)


@pytest_asyncio.fixture
async def super_admin_user(db_session: AsyncSession, institution: Institution) -> User:
    return await make_user(
        db_session, institution=institution, email="admin@x.test", role_name="super_admin"
    )


@pytest_asyncio.fixture
async def member_user(db_session: AsyncSession, institution: Institution) -> User:
    return await make_user(
        db_session, institution=institution, email="member@x.test", role_name="member"
    )


def auth_header(user: User) -> dict[str, str]:
    from src.shared.middleware.auth import create_access_token

    token = create_access_token(
        user_id=uuid.UUID(user.id),
        institution_id=uuid.UUID(user.institution_id),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_for() -> callable:  # type: ignore[misc]
    return auth_header
