"""HTTP integration tests for the media module.

Every endpoint runs the 7-point CLAUDE.md checklist where applicable:
happy · 401 · 403 · 400/422 · 404 · idempotent · Unicode. Plus a cross-
tenant fuzz test per `docs/design/tenant-isolation.md`.

The live storage layer and Redis are mocked via conftest fixtures; tests
don't depend on MinIO or Redis being up.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from src.modules.auth.models.db_models import User
from src.modules.media.models.db_models import MediaFile

API = "/api/v1"


def _idem_key() -> str:
    """Helper — a fresh UUID4 string for `Idempotency-Key`."""
    return str(uuid.uuid4())


def _init_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "file_name": "photo.jpg",
        "file_size": 245000,
        "mime_type": "image/jpeg",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# POST /media/upload/init
# ---------------------------------------------------------------------------


class TestInitUpload:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_storage: dict,
        fake_redis: Any,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/init",
            json=_init_body(),
            headers={
                **auth_headers(admin_user),
                "Idempotency-Key": _idem_key(),
            },
        )
        assert r.status_code == 201
        body = r.json()["data"]
        assert "upload_id" in body
        assert body["upload_url"].startswith("https://minio.test/")
        assert body["storage_key"].startswith(f"{admin_user.institution_id}/originals/")
        assert body["expires_in"] == 3600

    @pytest.mark.asyncio
    async def test_no_token_401(
        self,
        client: AsyncClient,
        mock_storage: dict,
        fake_redis: Any,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/init",
            json=_init_body(),
            headers={"Idempotency-Key": _idem_key()},
        )
        assert r.status_code in (401, 403)  # fastapi-security returns 403 on missing bearer

    @pytest.mark.asyncio
    async def test_missing_idempotency_key_400(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_storage: dict,
        fake_redis: Any,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/init",
            json=_init_body(),
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    @pytest.mark.asyncio
    async def test_bad_mime_type_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_storage: dict,
        fake_redis: Any,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/init",
            json=_init_body(mime_type="application/x-msdownload"),
            headers={
                **auth_headers(admin_user),
                "Idempotency-Key": _idem_key(),
            },
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_oversize_rejected_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_storage: dict,
        fake_redis: Any,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/init",
            json=_init_body(file_size=11 * 1024 * 1024),
            headers={
                **auth_headers(admin_user),
                "Idempotency-Key": _idem_key(),
            },
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_executable_ext_rejected_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_storage: dict,
        fake_redis: Any,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/init",
            json=_init_body(file_name="malware.exe"),
            headers={
                **auth_headers(admin_user),
                "Idempotency-Key": _idem_key(),
            },
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_body_field_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_storage: dict,
        fake_redis: Any,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/init",
            json={"file_name": "only.jpg"},  # missing file_size + mime_type
            headers={
                **auth_headers(admin_user),
                "Idempotency-Key": _idem_key(),
            },
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_idempotency_replay_returns_same_body(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_storage: dict,
        fake_redis: Any,
    ) -> None:
        key = _idem_key()
        headers = {**auth_headers(admin_user), "Idempotency-Key": key}
        body = _init_body()
        first = await client.post(f"{API}/media/upload/init", json=body, headers=headers)
        second = await client.post(f"{API}/media/upload/init", json=body, headers=headers)
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json() == second.json()

    @pytest.mark.asyncio
    async def test_idempotency_collision_returns_409(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_storage: dict,
        fake_redis: Any,
    ) -> None:
        key = _idem_key()
        headers = {**auth_headers(admin_user), "Idempotency-Key": key}
        first = await client.post(f"{API}/media/upload/init", json=_init_body(), headers=headers)
        assert first.status_code == 201
        collision = await client.post(
            f"{API}/media/upload/init",
            json=_init_body(file_name="different.jpg"),
            headers=headers,
        )
        assert collision.status_code == 409
        assert collision.json()["error"]["code"] == "IDEMPOTENCY_COLLISION"

    @pytest.mark.asyncio
    async def test_unicode_filename(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_storage: dict,
        fake_redis: Any,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/init",
            json=_init_body(file_name="छुट्टी_你好_🎉.jpg"),
            headers={
                **auth_headers(admin_user),
                "Idempotency-Key": _idem_key(),
            },
        )
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# POST /media/upload/complete
# ---------------------------------------------------------------------------


class TestCompleteUpload:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        pending_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/complete",
            json={"upload_id": pending_media.id},
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 200
        assert r.json()["data"]["upload_status"] == "completed"

    @pytest.mark.asyncio
    async def test_no_token_401(
        self,
        client: AsyncClient,
        pending_media: MediaFile,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/complete",
            json={"upload_id": pending_media.id},
        )
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_other_user_403(
        self,
        client: AsyncClient,
        other_user: User,
        auth_headers: Any,
        pending_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/complete",
            json={"upload_id": pending_media.id},
            headers=auth_headers(other_user),
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_unknown_id_404(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_storage: dict,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/complete",
            json={"upload_id": str(uuid.uuid4())},
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_not_in_s3_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        pending_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        mock_storage["head_returns"] = None
        r = await client.post(
            f"{API}/media/upload/complete",
            json={"upload_id": pending_media.id},
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_bad_upload_id_format_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        mock_storage: dict,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/complete",
            json={"upload_id": "not-a-uuid"},
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_cross_tenant_returns_404(
        self,
        client: AsyncClient,
        foreign_user: User,
        auth_headers: Any,
        pending_media: MediaFile,  # in institution A
        mock_storage: dict,
    ) -> None:
        r = await client.post(
            f"{API}/media/upload/complete",
            json={"upload_id": pending_media.id},
            headers=auth_headers(foreign_user),
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_idempotent_double_complete(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        completed_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        """Second /complete on an already-completed row is a no-op 200."""
        r = await client.post(
            f"{API}/media/upload/complete",
            json={"upload_id": completed_media.id},
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 200
        assert r.json()["data"]["upload_status"] == "completed"


# ---------------------------------------------------------------------------
# GET /media/{id}
# ---------------------------------------------------------------------------


class TestGetMedia:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        completed_media: MediaFile,
    ) -> None:
        r = await client.get(
            f"{API}/media/{completed_media.id}",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 200
        assert r.json()["data"]["id"] == completed_media.id

    @pytest.mark.asyncio
    async def test_no_token_401(
        self,
        client: AsyncClient,
        completed_media: MediaFile,
    ) -> None:
        r = await client.get(f"{API}/media/{completed_media.id}")
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_bad_id_format_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
    ) -> None:
        r = await client.get(
            f"{API}/media/not-a-uuid",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_id_404(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
    ) -> None:
        r = await client.get(
            f"{API}/media/{uuid.uuid4()}",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_tenant_returns_404(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        foreign_media: MediaFile,
    ) -> None:
        """User A must not see institution B's media — 404 not 403."""
        r = await client.get(
            f"{API}/media/{foreign_media.id}",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /media/{id}/download
# ---------------------------------------------------------------------------


class TestDownload:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        completed_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        r = await client.get(
            f"{API}/media/{completed_media.id}/download",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["download_url"].startswith("https://minio.test/")
        assert body["expires_in"] == 3600

    @pytest.mark.asyncio
    async def test_no_token_401(
        self,
        client: AsyncClient,
        completed_media: MediaFile,
    ) -> None:
        r = await client.get(f"{API}/media/{completed_media.id}/download")
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_pending_returns_422(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        pending_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        r = await client.get(
            f"{API}/media/{pending_media.id}/download",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_id_404(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
    ) -> None:
        r = await client.get(
            f"{API}/media/{uuid.uuid4()}/download",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_tenant_returns_404(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        foreign_media: MediaFile,
    ) -> None:
        r = await client.get(
            f"{API}/media/{foreign_media.id}/download",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /media/{id}
# ---------------------------------------------------------------------------


class TestDeleteMedia:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        completed_media: MediaFile,
    ) -> None:
        r = await client.delete(
            f"{API}/media/{completed_media.id}",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["id"] == completed_media.id
        assert body["recycle_bin_at"] is not None

    @pytest.mark.asyncio
    async def test_no_token_401(
        self,
        client: AsyncClient,
        completed_media: MediaFile,
    ) -> None:
        r = await client.delete(f"{API}/media/{completed_media.id}")
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_non_uploader_403(
        self,
        client: AsyncClient,
        other_user: User,
        auth_headers: Any,
        completed_media: MediaFile,
    ) -> None:
        r = await client.delete(
            f"{API}/media/{completed_media.id}",
            headers=auth_headers(other_user),
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_unknown_id_404(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
    ) -> None:
        r = await client.delete(
            f"{API}/media/{uuid.uuid4()}",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_tenant_returns_404(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        foreign_media: MediaFile,
    ) -> None:
        r = await client.delete(
            f"{API}/media/{foreign_media.id}",
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_double_delete_is_idempotent(
        self,
        client: AsyncClient,
        admin_user: User,
        auth_headers: Any,
        completed_media: MediaFile,
    ) -> None:
        h = auth_headers(admin_user)
        first = await client.delete(f"{API}/media/{completed_media.id}", headers=h)
        second = await client.delete(f"{API}/media/{completed_media.id}", headers=h)
        assert first.status_code == 200
        assert second.status_code == 200
        # Timestamp doesn't move on the second call.
        assert first.json()["data"]["recycle_bin_at"] == second.json()["data"]["recycle_bin_at"]
