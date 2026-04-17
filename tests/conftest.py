"""Shared pytest fixtures for DuttaMessenger.

Provides isolated per-test database, async HTTP client, authenticated headers,
and factory-boy factory access. Every integration test starts in a nested
transaction that is rolled back on teardown, so tests never see each other's
data and run in any order.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
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


def _load_dotenv() -> None:
    """Populate os.environ from the repo root .env if present.

    Must run before `src.config` is imported so `Settings()` picks up the
    right DATABASE_URL / SECRET_KEY / etc. Fixtures that read os.environ
    directly (e.g. TEST_DATABASE_URL below) see the same values.
    """
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()


# ---------------------------------------------------------------------------
# Test DB URL — prefer a dedicated test database so integration tests never
# accidentally touch the dev DB. Override via TEST_DATABASE_URL in .env.
# Default matches the local Homebrew Postgres recipe (docs/LOCAL_SETUP.md):
# role = current Mac username, no password.
# ---------------------------------------------------------------------------
_default_user = os.environ.get("USER", "postgres")
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql+asyncpg://{_default_user}@localhost:5432/dutta_messenger_test",
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

    Accepts a SQLAlchemy model, a dict with `id`/`institution_id` keys, or any
    object with `.id` and `.institution_id` attributes.
    """

    def _make(user: Any) -> dict[str, str]:
        from src.shared.middleware.auth import create_access_token

        user_id = user["id"] if isinstance(user, dict) else user.id
        inst_id = (
            user["institution_id"]
            if isinstance(user, dict)
            else user.institution_id
        )
        token = create_access_token(
            user_id=uuid.UUID(str(user_id)),
            institution_id=uuid.UUID(str(inst_id)),
        )
        return {"Authorization": f"Bearer {token}"}

    return _make
