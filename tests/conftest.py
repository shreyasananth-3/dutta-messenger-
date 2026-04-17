"""Shared pytest fixtures for DuttaMessenger.

Provides isolated per-test database, async HTTP client, authenticated headers,
and factory-boy factory access. Every integration test starts in a nested
transaction that is rolled back on teardown, so tests never see each other's
data and run in any order.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ---------------------------------------------------------------------------
# Test DB URL — prefer a dedicated test database so integration tests never
# accidentally touch the dev DB. Override via TEST_DATABASE_URL.
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://messenger:messenger_pass@localhost:5432/dutta_messenger_test",
)


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Session-scoped event loop required by async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """One engine per test session — pool overhead is paid once."""
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session inside a transaction that is rolled back on teardown.

    Pattern (SQLAlchemy 2.x async): open an outer connection + transaction,
    bind a session to it, use a SAVEPOINT so service code's `commit()` calls
    don't break isolation, and roll back the outer transaction at the end.
    Tests therefore never see each other's data and can run in any order.
    """
    async with test_engine.connect() as connection:
        outer = await connection.begin()
        factory = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with factory() as session:
            await connection.begin_nested()
            try:
                yield session
            finally:
                await session.close()
                await outer.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTPX AsyncClient wired to the FastAPI app with the test DB session.

    Overrides the `get_db` dependency so every request uses the rolled-back
    test session.
    """
    # Local import so `src.main` can rely on fixtures being in place first.
    from src.main import app
    from src.shared.database import get_db

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers() -> Any:
    """Factory fixture: `auth_headers(user)` returns a Bearer-token header dict.

    Kept here so every module's tests use the same helper.
    """

    def _make(user: Any) -> dict[str, str]:
        # Lazy import to avoid circular imports during collection.
        import uuid as _uuid

        from src.shared.middleware.auth import create_access_token

        token = create_access_token(
            user_id=_uuid.UUID(str(user.id)),
            institution_id=_uuid.UUID(str(user.institution_id)),
        )
        return {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture(autouse=True)
def _freeze_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a deterministic JWT secret in tests for reproducibility."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-do-not-use-in-prod")
