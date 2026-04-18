"""Pytest fixtures scoped to the notifications module's tests.

Builds a dedicated FastAPI app that mounts only the notifications router
plus the `AppException` handler from the main app. Avoids toggling the
`ENABLE_NOTIFICATIONS` flag at module import time (which would be order-
sensitive since `src.main` caches the global app on first import).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.notifications.router import router as notifications_router
from src.modules.notifications.tasks import push_task as push_task_module
from src.shared.database import get_db
from src.shared.exceptions import AppException
from tests.modules.notifications.factories import MockFcmClient


def _build_test_app() -> FastAPI:
    """Assemble a minimal FastAPI app with the notifications router mounted."""
    app = FastAPI(title="DuttaMessenger (notifications test app)")

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

    app.include_router(notifications_router, prefix="/api/v1")
    return app


@pytest_asyncio.fixture
async def notif_client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    """HTTPX client wired to the minimal notifications app + test DB session."""
    app = _build_test_app()

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def no_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop `FanoutService.dispatch_message_notifications` from touching Celery.

    The fanout service calls `_enqueue_batch(batch_id, institution_id)`
    which in turn calls `send_push_batch.delay(...)`. Tests that cover
    fanout logic don't need (or have) a broker — we patch the enqueue
    helper so it records calls in a list.
    """
    from src.modules.notifications.services import fanout_service

    captured: list[tuple[str, str]] = []

    def _capture(batch_id: str, institution_id: object) -> None:
        captured.append((str(batch_id), str(institution_id)))

    monkeypatch.setattr(fanout_service, "_enqueue_batch", _capture)
    monkeypatch.setattr(
        fanout_service.FanoutService,
        "_captured_enqueues",
        captured,
        raising=False,
    )


@pytest.fixture
def mock_fcm_client(monkeypatch: pytest.MonkeyPatch) -> MockFcmClient:
    """Swap in a `MockFcmClient` for every push-task test case."""
    client = MockFcmClient()
    monkeypatch.setattr(push_task_module, "_client", client)
    return client
