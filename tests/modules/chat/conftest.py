"""Pytest fixtures for chat module tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.auth.services.auth_service import AuthService
from src.modules.chat.router import router as chat_router
from src.modules.chat.services.message_service import MessageService
from src.modules.groups.services.group_service import GroupService
from src.shared.database import get_db
from src.shared.exceptions import AppException


def _build_test_app() -> FastAPI:
    app = FastAPI(title="DuttaMessenger (chat test app)")

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

    app.include_router(chat_router, prefix="/api/v1")
    return app


@pytest_asyncio.fixture
async def chat_client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
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
    await db_session.flush()
    return inst


async def make_user(
    db_session: AsyncSession, *, institution: Institution, email: str | None = None
) -> User:
    user = await AuthService.register_user(
        db_session,
        institution_id=institution.id,
        email=email or f"u-{uuid.uuid4().hex[:6]}@x.test",
        password="Sup3rStr0ng!",
        full_name="Test User",
    )
    await db_session.flush()
    return user


async def make_group_and_conv(
    db_session: AsyncSession,
    *,
    institution: Institution,
    creator: User,
    mode: str = "simple",
):
    """Create a group and ensure its conversation exists."""
    group = await GroupService.create_group(
        db_session,
        institution_id=institution.id,
        creator_id=creator.id,
        name=f"G-{uuid.uuid4().hex[:6]}",
        mode=mode,
    )
    topic_id = None
    if mode == "topics":
        from sqlalchemy import select

        from src.modules.groups.models.db_models import Topic

        row = (
            (await db_session.execute(select(Topic).where(Topic.group_id == group.id)))
            .scalars()
            .first()
        )
        topic_id = row.id if row else None
    conv = await MessageService.open_conversation(
        db_session,
        institution_id=institution.id,
        actor_id=creator.id,
        group_id=group.id,
        topic_id=topic_id,
    )
    await db_session.flush()
    return group, conv


@pytest_asyncio.fixture
async def institution(db_session: AsyncSession) -> Institution:
    return await make_institution(db_session)


@pytest_asyncio.fixture
async def alice(db_session: AsyncSession, institution: Institution) -> User:
    return await make_user(db_session, institution=institution, email="alice@x.test")


@pytest_asyncio.fixture
async def bob(db_session: AsyncSession, institution: Institution) -> User:
    return await make_user(db_session, institution=institution, email="bob@x.test")


def auth_header(user: User) -> dict[str, str]:
    from src.shared.middleware.auth import create_access_token

    token = create_access_token(
        user_id=uuid.UUID(user.id),
        institution_id=uuid.UUID(user.institution_id),
    )
    return {"Authorization": f"Bearer {token}"}
