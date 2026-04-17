"""Factory helpers for the notifications module.

Factories here are thin wrappers around the service layer so they exercise
real code paths (audit writes, validators) instead of inserting rows via
raw SQL. Keeps test setup honest.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.auth.services.auth_service import AuthService
from src.modules.notifications.models.db_models import FcmToken
from src.modules.notifications.services.token_service import TokenService

VALID_PASSWORD = "Sup3rStr0ng!"


async def make_institution(db: AsyncSession, *, name: str | None = None) -> Institution:
    """Create an institution via `AuthService`, flushed but uncommitted."""
    inst = await AuthService.create_institution(
        db,
        name=name or f"Inst-{uuid.uuid4().hex[:8]}",
        domain=f"{uuid.uuid4().hex[:6]}.test",
    )
    await db.flush()
    return inst


async def make_user(
    db: AsyncSession,
    *,
    institution: Institution,
    email: str | None = None,
) -> User:
    """Register a user inside a pre-existing institution."""
    user = await AuthService.register_user(
        db,
        institution_id=institution.id,
        email=email or f"user-{uuid.uuid4().hex[:6]}@school.test",
        password=VALID_PASSWORD,
        full_name="Test User",
    )
    await db.flush()
    return user


async def make_token(
    db: AsyncSession,
    *,
    user: User,
    institution: Institution,
    token_string: str | None = None,
    device_type: str = "android",
) -> FcmToken:
    """Register an FCM token via the real service path."""
    row, _reused = await TokenService.register_token(
        db,
        user_id=uuid.UUID(user.id),
        institution_id=uuid.UUID(institution.id),
        token=token_string or f"fcm-{uuid.uuid4().hex}",
        device_name="pytest",
        device_type=device_type,
    )
    return row


class MockFcmResult:
    """Fixed multicast response used by test cases.

    Not a dataclass because the fanout service only reads the four attrs
    below; a richer mirror would duplicate `FcmResponse`.
    """

    def __init__(
        self,
        *,
        success_count: int = 1,
        failure_count: int = 0,
        unregistered_tokens: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        self.success_count = success_count
        self.failure_count = failure_count
        self.unregistered_tokens = unregistered_tokens or []
        self.error = error


class MockFcmClient:
    """Captures multicast calls and returns canned responses in FIFO order."""

    def __init__(self, responses: list[MockFcmResult] | None = None) -> None:
        self.responses = list(responses) if responses else [MockFcmResult()]
        self.calls: list[dict[str, Any]] = []

    def send_multicast(
        self,
        *,
        tokens: list[str],
        title: str,
        body: str,
        data: dict[str, Any] | None,
    ) -> MockFcmResult:
        self.calls.append(
            {"tokens": list(tokens), "title": title, "body": body, "data": data}
        )
        if len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)


def fresh_token_string() -> str:
    """Return a unique FCM-like token string for use in a test."""
    return f"fcm-{uuid.uuid4().hex}-{uuid.uuid4().hex}"
