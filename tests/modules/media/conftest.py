"""Shared fixtures for media-module tests.

Two-institution layout mirrors the users-module pattern so cross-tenant fuzz
tests slot in without reinventing seeding logic. The storage layer and Redis
are mocked module-wide so tests never hit a live MinIO / Redis.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.auth.services.auth_service import AuthService
from src.modules.media.models.db_models import MediaFile

_PASSWORD = "Sup3rStr0ngP@ss!"


# ---------------------------------------------------------------------------
# Institutions + users
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def institution(db_session: AsyncSession) -> Institution:
    inst = await AuthService.create_institution(
        db_session,
        name=f"MediaSchool {uuid.uuid4().hex[:8]}",
        domain=f"{uuid.uuid4().hex[:6]}.media.test",
    )
    await db_session.flush()
    return inst


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession, institution: Institution) -> User:
    """Uploader in institution A."""
    user = await AuthService.register_user(
        db_session,
        institution_id=institution.id,
        email="uploader@media-test.test",
        password=_PASSWORD,
        full_name="Uploader User",
    )
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def other_user(
    db_session: AsyncSession, institution: Institution, admin_user: User
) -> User:
    """A second user in the SAME institution, used for 403-on-delete tests."""
    user = await AuthService.register_user(
        db_session,
        institution_id=institution.id,
        email="other@media-test.test",
        password=_PASSWORD,
        full_name="Other Same-Tenant Person",
    )
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def foreign_institution(db_session: AsyncSession) -> Institution:
    inst = await AuthService.create_institution(
        db_session,
        name=f"MediaForeign {uuid.uuid4().hex[:8]}",
        domain=f"{uuid.uuid4().hex[:6]}.media-foreign.test",
    )
    await db_session.flush()
    return inst


@pytest_asyncio.fixture
async def foreign_user(
    db_session: AsyncSession, foreign_institution: Institution
) -> User:
    user = await AuthService.register_user(
        db_session,
        institution_id=foreign_institution.id,
        email="foreigner@media-test.test",
        password=_PASSWORD,
        full_name="Foreign User",
    )
    await db_session.flush()
    return user


# ---------------------------------------------------------------------------
# Storage mocks — no live MinIO during tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_storage(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace every `src.shared.storage.*` call used by media_service.

    Returns a mutable dict so individual tests can override the defaults
    (e.g. force `head_object` to return None to simulate a missing upload).
    """
    from src.modules.media.services import media_service as svc
    from src.shared import storage

    state: dict[str, Any] = {
        "last_put_call": None,
        "last_get_call": None,
        "head_returns": {"ContentLength": 12345, "ContentType": "image/jpeg"},
        "head_calls": 0,
    }

    async def _put(
        key: str,
        *,
        content_type: str,
        content_length_max: int | None = None,
        expires_in: int = 3600,
        bucket: str | None = None,
    ) -> str:
        state["last_put_call"] = {
            "key": key,
            "content_type": content_type,
            "content_length_max": content_length_max,
            "expires_in": expires_in,
        }
        return f"https://minio.test/{key}?put-signed=yes"

    async def _get(
        key: str,
        *,
        expires_in: int = 3600,
        bucket: str | None = None,
        response_content_disposition: str | None = None,
    ) -> str:
        state["last_get_call"] = {
            "key": key,
            "expires_in": expires_in,
            "content_disposition": response_content_disposition,
        }
        return f"https://minio.test/{key}?get-signed=yes"

    async def _head(key: str, *, bucket: str | None = None) -> Any:
        state["head_calls"] += 1
        return state["head_returns"]

    async def _delete(key: str, *, bucket: str | None = None) -> None:
        state.setdefault("deleted_keys", []).append(key)

    # Patch both the importing module (media_service) and the source module
    # so direct references in either location see the fake.
    for target in (storage, svc.storage):
        monkeypatch.setattr(target, "presigned_put_url", _put)
        monkeypatch.setattr(target, "presigned_get_url", _get)
        monkeypatch.setattr(target, "head_object", _head)
        monkeypatch.setattr(target, "delete_object", _delete)
    return state


# ---------------------------------------------------------------------------
# Idempotency Redis mock — an in-process store
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal dict-backed stand-in for `redis.asyncio.Redis`.

    Implements just enough of the Redis async API for the idempotency
    middleware (`get`, `set` with TTL argument). Ignores TTL — good enough
    for a single test run.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: Any, *, ex: int | None = None) -> bool:
        self.store[key] = value if isinstance(value, (bytes, str)) else str(value)
        return True


@pytest_asyncio.fixture
async def fake_redis(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_FakeRedis]:
    """Swap the idempotency middleware's Redis for an in-process fake."""
    from src.shared.middleware import idempotency as idem_mod

    fake = _FakeRedis()

    async def _get_redis() -> _FakeRedis:
        return fake

    monkeypatch.setattr(idem_mod, "get_redis", _get_redis)
    yield fake


# ---------------------------------------------------------------------------
# Domain fixtures — pre-seeded media rows
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pending_media(
    db_session: AsyncSession,
    institution: Institution,
    admin_user: User,
) -> MediaFile:
    """A row in `pending` state — no S3 verify yet."""
    media = MediaFile(
        id=str(uuid.uuid4()),
        institution_id=institution.id,
        uploader_id=admin_user.id,
        file_name="photo.jpg",
        file_size=245000,
        mime_type="image/jpeg",
        storage_key=(
            f"{institution.id}/originals/2026/04/"
            f"{uuid.uuid4()}.jpg"
        ),
        upload_status="pending",
    )
    db_session.add(media)
    await db_session.flush()
    return media


@pytest_asyncio.fixture
async def completed_media(
    db_session: AsyncSession,
    institution: Institution,
    admin_user: User,
) -> MediaFile:
    """A row in `completed` state — download / delete tests use this."""
    media = MediaFile(
        id=str(uuid.uuid4()),
        institution_id=institution.id,
        uploader_id=admin_user.id,
        file_name="report.pdf",
        file_size=512000,
        mime_type="application/pdf",
        storage_key=(
            f"{institution.id}/originals/2026/04/"
            f"{uuid.uuid4()}.pdf"
        ),
        upload_status="completed",
    )
    db_session.add(media)
    await db_session.flush()
    return media


@pytest_asyncio.fixture
async def foreign_media(
    db_session: AsyncSession,
    foreign_institution: Institution,
    foreign_user: User,
) -> MediaFile:
    """A completed row owned by a DIFFERENT tenant — cross-tenant fuzz."""
    media = MediaFile(
        id=str(uuid.uuid4()),
        institution_id=foreign_institution.id,
        uploader_id=foreign_user.id,
        file_name="secret.pdf",
        file_size=4096,
        mime_type="application/pdf",
        storage_key=(
            f"{foreign_institution.id}/originals/2026/04/"
            f"{uuid.uuid4()}.pdf"
        ),
        upload_status="completed",
    )
    db_session.add(media)
    await db_session.flush()
    return media
