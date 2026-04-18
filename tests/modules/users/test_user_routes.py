"""HTTP integration tests for the users module.

Every endpoint runs the 7-point CLAUDE.md checklist where applicable:
happy · 401 · 403 · 400/422 · 404 · idempotent · Unicode. Plus a
cross-tenant fuzz test per `docs/design/tenant-isolation.md`.

Redis is mocked via monkeypatch on presence_service (the same trick as
test_presence_service.py) so tests don't depend on a running Redis for
online-status checks.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient

from src.modules.auth.models.db_models import User
from src.modules.users.services import presence_service

API = "/api/v1"


@pytest_asyncio.fixture
async def mock_redis(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """All routes that touch presence_service see a mocked Redis.

    The pipeline's `execute()` returns a list sized to the number of
    `.exists()` calls queued since the last execute, so the `zip(strict=True)`
    in `presence_service.get_online_map` never fails for length reasons.
    Tests that care about online/offline can preload the values by setting
    `client.pipeline.return_value._preload = [...]`.
    """
    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.exists = AsyncMock(return_value=0)

    pipeline = MagicMock()
    pipeline._preload = None
    pipeline._queued = 0

    def _exists_queue(*_a: Any, **_kw: Any) -> MagicMock:
        pipeline._queued += 1
        return pipeline

    async def _execute() -> list[int]:
        n = pipeline._queued
        pipeline._queued = 0
        if pipeline._preload is not None:
            preload = pipeline._preload
            pipeline._preload = None
            return list(preload)
        return [0] * n

    pipeline.exists = MagicMock(side_effect=_exists_queue)
    pipeline.execute = AsyncMock(side_effect=_execute)
    client.pipeline = MagicMock(return_value=pipeline)

    async def _get_redis() -> MagicMock:
        return client

    monkeypatch.setattr(presence_service, "get_redis", _get_redis)
    return client


# ---------------------------------------------------------------------------
# GET /users/me
# ---------------------------------------------------------------------------


class TestGetMe:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        r = await client.get(f"{API}/users/me", headers=auth_headers(admin_user))
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["id"] == admin_user.id
        assert body["email"] == admin_user.email
        assert body["is_online"] is False

    @pytest.mark.asyncio
    async def test_no_token_401(self, client: AsyncClient) -> None:
        r = await client.get(f"{API}/users/me")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_bad_token_401(self, client: AsyncClient) -> None:
        r = await client.get(
            f"{API}/users/me",
            headers={"Authorization": "Bearer garbage.garbage.garbage"},
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /users/me
# ---------------------------------------------------------------------------


class TestUpdateMe:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        r = await client.patch(
            f"{API}/users/me",
            headers=auth_headers(admin_user),
            json={"bio": "Testing bio", "full_name": "Admin Renamed"},
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["full_name"] == "Admin Renamed"
        assert data["bio"] == "Testing bio"

    @pytest.mark.asyncio
    async def test_no_token_401(self, client: AsyncClient) -> None:
        r = await client.patch(f"{API}/users/me", json={"bio": "x"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_bio_over_500_chars_returns_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        r = await client.patch(
            f"{API}/users/me",
            headers=auth_headers(admin_user),
            json={"bio": "x" * 501},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_unicode_bio(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        unicode_bio = "Hello 😀 नमस्ते 你好 مرحبا"
        r = await client.patch(
            f"{API}/users/me",
            headers=auth_headers(admin_user),
            json={"bio": unicode_bio},
        )
        assert r.status_code == 200
        assert r.json()["data"]["bio"] == unicode_bio

    @pytest.mark.asyncio
    async def test_empty_body_is_noop(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        r = await client.patch(
            f"{API}/users/me",
            headers=auth_headers(admin_user),
            json={},
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_idempotent_twice(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        """Sending the same PATCH twice produces the same state."""
        payload = {"bio": "Idempotent"}
        r1 = await client.patch(f"{API}/users/me", headers=auth_headers(admin_user), json=payload)
        r2 = await client.patch(f"{API}/users/me", headers=auth_headers(admin_user), json=payload)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["data"]["bio"] == r2.json()["data"]["bio"]


# ---------------------------------------------------------------------------
# GET /users/{id}
# ---------------------------------------------------------------------------


class TestGetUser:
    @pytest.mark.asyncio
    async def test_happy_path_strips_email(
        self,
        client: AsyncClient,
        admin_user: User,
        other_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        r = await client.get(
            f"{API}/users/{other_user.id}",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["id"] == other_user.id
        # /users/{id} strips email — only /me returns it
        assert data["email"] is None

    @pytest.mark.asyncio
    async def test_no_token_401(self, client: AsyncClient) -> None:
        r = await client.get(f"{API}/users/{uuid.uuid4()}")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_id_404(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        r = await client.get(
            f"{API}/users/{uuid.uuid4()}",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_malformed_uuid_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
    ) -> None:
        r = await client.get(
            f"{API}/users/not-a-uuid",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_cross_tenant_returns_404(
        self,
        client: AsyncClient,
        admin_user: User,
        foreign_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        """Institution A user cannot look up an institution B user — 404 not 403."""
        r = await client.get(
            f"{API}/users/{foreign_user.id}",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /users/search
# ---------------------------------------------------------------------------


class TestSearchRoute:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        client: AsyncClient,
        admin_user: User,
        other_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        r = await client.get(
            f"{API}/users/search",
            headers=auth_headers(admin_user),
            params={"q": "Other"},
        )
        assert r.status_code == 200
        results = r.json()["data"]["results"]
        assert any(u["id"] == other_user.id for u in results)

    @pytest.mark.asyncio
    async def test_no_token_401(self, client: AsyncClient) -> None:
        r = await client.get(f"{API}/users/search", params={"q": "x"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_query_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
    ) -> None:
        r = await client.get(
            f"{API}/users/search",
            headers=auth_headers(admin_user),
            params={"q": ""},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_limit_out_of_range_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
    ) -> None:
        r = await client.get(
            f"{API}/users/search",
            headers=auth_headers(admin_user),
            params={"q": "a", "limit": 999},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_unicode_query_ok(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        r = await client.get(
            f"{API}/users/search",
            headers=auth_headers(admin_user),
            params={"q": "你好"},
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_cross_tenant_excluded(
        self,
        client: AsyncClient,
        admin_user: User,
        foreign_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        r = await client.get(
            f"{API}/users/search",
            headers=auth_headers(admin_user),
            params={"q": "Foreign"},
        )
        assert r.status_code == 200
        ids = {u["id"] for u in r.json()["data"]["results"]}
        assert foreign_user.id not in ids


# ---------------------------------------------------------------------------
# GET /users/online
# ---------------------------------------------------------------------------


class TestOnlineRoute:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        client: AsyncClient,
        admin_user: User,
        other_user: User,
        auth_headers: Any,
        mock_redis: MagicMock,
    ) -> None:
        mock_redis.pipeline.return_value._preload = [1, 0]
        r = await client.get(
            f"{API}/users/online",
            headers=auth_headers(admin_user),
            params=[("user_ids", admin_user.id), ("user_ids", other_user.id)],
        )
        assert r.status_code == 200
        data = r.json()["data"]["online"]
        assert data[admin_user.id] is True
        assert data[other_user.id] is False

    @pytest.mark.asyncio
    async def test_no_token_401(self, client: AsyncClient) -> None:
        r = await client.get(
            f"{API}/users/online",
            params=[("user_ids", str(uuid.uuid4()))],
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_list_422(
        self, client: AsyncClient, admin_user: User, auth_headers: Any
    ) -> None:
        r = await client.get(f"{API}/users/online", headers=auth_headers(admin_user))
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /users/{id}/status deliberately deferred to Stage 4b (ACL).
# No tests here — they land with the route when ACL wires in.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET + PATCH /users/me/settings
# ---------------------------------------------------------------------------


class TestSettingsRoute:
    @pytest.mark.asyncio
    async def test_get_seeds_defaults(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
    ) -> None:
        r = await client.get(
            f"{API}/users/me/settings",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["theme"] == "system"
        assert data["language"] == "en"
        assert data["notification_messages"] is True

    @pytest.mark.asyncio
    async def test_get_no_token_401(self, client: AsyncClient) -> None:
        r = await client.get(f"{API}/users/me/settings")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_patch_happy(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
    ) -> None:
        r = await client.patch(
            f"{API}/users/me/settings",
            headers=auth_headers(admin_user),
            json={"theme": "dark", "language": "hi"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["theme"] == "dark"

    @pytest.mark.asyncio
    async def test_patch_bad_theme_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
    ) -> None:
        r = await client.patch(
            f"{API}/users/me/settings",
            headers=auth_headers(admin_user),
            json={"theme": "chartreuse"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_idempotent(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
    ) -> None:
        payload = {"theme": "dark"}
        r1 = await client.patch(
            f"{API}/users/me/settings",
            headers=auth_headers(admin_user),
            json=payload,
        )
        r2 = await client.patch(
            f"{API}/users/me/settings",
            headers=auth_headers(admin_user),
            json=payload,
        )
        assert r1.json()["data"]["theme"] == r2.json()["data"]["theme"] == "dark"
